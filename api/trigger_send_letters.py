"""
Vercel Serverless Function - Dispatch the Send Renewal Letters workflow.

Endpoint: POST /api/trigger_send_letters

Body:
  {
    "password": "TaliRenewals123457$",
    "dryRun":   true | false,
    "letters":  [
      {"property": "49THSTR", "month": "2026-12", "unit": "1015",
       "resident": "Jane Doe"},
      ...
    ]
  }

Behavior:
  - Validates Tali password.
  - Generates a short runId (UUID-ish) that doubles as the status filename.
  - Fires GitHub repository_dispatch (event type: "send-renewal-letters")
    with the payload. The workflow writes send-status/<runId>.json which the
    dashboard polls as a static asset.

Response:
  200 { "ok": true, "runId": "...", "dispatchedAt": "..." }
  403 incorrect password
  400 malformed body
  502 GitHub dispatch failed

Env:
  GITHUB_TOKEN  -- fine-grained PAT with Actions:write + Contents:write
                   on ymi-flowing/sns-daily-report
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import secrets
import urllib.request
import urllib.error
from datetime import datetime


REPO = "ymi-flowing/sns-daily-report"
EVENT_TYPE = "send-renewal-letters"
EDIT_PASSWORD = "TaliRenewals123457$"
MAX_LETTERS = 500  # soft cap -- client_payload is 64KB total


def _new_run_id() -> str:
    """Short, URL-safe, filesystem-safe run id."""
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{secrets.token_hex(3)}"


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return self._send(500, {"ok": False, "error": "GITHUB_TOKEN not configured"})

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return self._send(400, {"ok": False, "error": "invalid JSON body"})

        if body.get("password") != EDIT_PASSWORD:
            return self._send(403, {"ok": False, "error": "incorrect password"})

        letters = body.get("letters")
        if not isinstance(letters, list) or not letters:
            return self._send(400, {"ok": False, "error": "letters must be a non-empty list"})
        if len(letters) > MAX_LETTERS:
            return self._send(400, {"ok": False, "error": f"too many letters ({len(letters)} > {MAX_LETTERS})"})

        # Normalise each letter to only the fields the workflow needs. Keeps
        # the client_payload compact (64KB total via GitHub).
        normed = []
        for L in letters:
            prop = str(L.get("property", "")).strip()
            month = str(L.get("month", "")).strip()
            unit = str(L.get("unit", "")).strip()
            resident = str(L.get("resident", "")).strip()
            if not prop or not month or not unit:
                continue
            normed.append({
                "property": prop,
                "month": month,
                "unit": unit,
                "resident": resident,
            })
        if not normed:
            return self._send(400, {"ok": False, "error": "no valid letters after normalization"})

        run_id = _new_run_id()
        dry_run = bool(body.get("dryRun", False))
        client_payload = {
            "runId": run_id,
            "dryRun": dry_run,
            "letters": normed,
        }

        url = f"https://api.github.com/repos/{REPO}/dispatches"
        req_body = json.dumps({
            "event_type": EVENT_TYPE,
            "client_payload": client_payload,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=req_body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SNS-Renewals-Send-Dispatch")
        req.add_header("Content-Type", "application/json")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            if resp.status == 204:
                return self._send(200, {
                    "ok": True,
                    "runId": run_id,
                    "statusUrl": f"/send-status/{run_id}.json",
                    "dispatchedAt": datetime.utcnow().isoformat() + "Z",
                    "total": len(normed),
                    "dryRun": dry_run,
                })
            return self._send(resp.status, {"ok": False, "error": f"Unexpected status {resp.status}"})
        except urllib.error.HTTPError as e:
            return self._send(e.code, {"ok": False, "error": f"GitHub API {e.code}: {e.read().decode()}"})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})
