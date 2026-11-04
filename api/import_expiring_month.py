"""
Vercel Serverless Function - Trigger the Import Expiring Month workflow.

Endpoint: POST /api/import_expiring_month

Body:
  { "password": "<edit password>", "month": "YYYY-MM" }

Fires workflow_dispatch on .github/workflows/import-expiring-month.yml, which
appends new expiring-lease rows for the given month and rebakes
renewals_data.json. The client should then poll /api/import_status for
completion.

Gated by the same edit password as /api/update_adjustments_batch.

Env:
  GITHUB_TOKEN   -- fine-grained PAT with Actions:write on this repo
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime


REPO = "ymi-flowing/sns-daily-report"
WORKFLOW_FILE = "import-expiring-month.yml"
EDIT_PASSWORD = "Tali124$"


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
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
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return self._send(400, {"success": False, "error": "invalid JSON body"})

        if body.get("password") != EDIT_PASSWORD:
            return self._send(403, {"success": False, "error": "incorrect password"})

        month = str(body.get("month") or "").strip()
        if not re.match(r"^\d{4}-\d{2}$", month):
            return self._send(400, {"success": False, "error": "month must be YYYY-MM"})

        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return self._send(500, {"success": False, "error": "GITHUB_TOKEN not configured"})

        url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        payload = json.dumps({"ref": "main", "inputs": {"month": month}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SNS-Import-Trigger")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            if resp.status == 204:
                self._send(200, {
                    "success": True,
                    "message": f"Import workflow dispatched for {month}",
                    "month": month,
                    "dispatchedAt": datetime.utcnow().isoformat() + "Z",
                })
            else:
                self._send(resp.status, {"success": False, "error": f"Unexpected status {resp.status}"})
        except urllib.error.HTTPError as e:
            self._send(e.code, {"success": False, "error": f"GitHub API {e.code}: {e.read().decode()}"})
        except Exception as e:
            self._send(500, {"success": False, "error": str(e)})
