"""
Vercel Serverless Function - Read a Send Renewal Letters run's status file.

Fetches send-status/<runId>.json directly from GitHub's Contents API so the
dashboard polls fresh state regardless of Vercel's deploy / edge cache. The
workflow commits updates to main every ~30s; this endpoint reflects them
within a couple of seconds.

Endpoint: GET /api/send_progress?runId=<id>

Response:
  200 { "ok": true, "status": "running" | "complete" | "failed", ...fields }
  404 { "ok": false, "error": "no status yet" }
  400 missing runId
  502 upstream failure

Env:
  GITHUB_TOKEN -- same PAT used by trigger_send_letters
"""
from http.server import BaseHTTPRequestHandler
import base64
import json
import os
import re
import urllib.parse
import urllib.request
import urllib.error


REPO = "ymi-flowing/sns-daily-report"


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return self._send(500, {"ok": False, "error": "GITHUB_TOKEN not configured"})

        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        run_id = (params.get("runId") or [""])[0].strip()
        if not run_id or not re.fullmatch(r"[A-Za-z0-9_\-\.]+", run_id):
            return self._send(400, {"ok": False, "error": "missing or invalid runId"})

        path = f"send-status/{run_id}.json"
        url = f"https://api.github.com/repos/{REPO}/contents/{urllib.parse.quote(path)}?ref=main"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SNS-Renewals-SendProgress")

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode())
            content = base64.b64decode(data["content"])
            status = json.loads(content.decode("utf-8"))
            status["ok"] = True
            return self._send(200, status)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return self._send(404, {"ok": False, "error": "no status yet (file not committed)"})
            return self._send(e.code, {"ok": False, "error": f"GitHub API {e.code}: {e.read().decode()[:200]}"})
        except Exception as e:
            return self._send(502, {"ok": False, "error": str(e)})
