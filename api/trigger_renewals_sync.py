"""
Vercel Serverless Function - Trigger the Sync Renewals GitHub Actions workflow.

Endpoint: POST /api/trigger_renewals_sync

Fires workflow_dispatch on .github/workflows/sync-renewals.yml, which runs
sync_all.py (ResMan -> Google Sheet). The client should then poll
/api/sync_status for completion before re-fetching /api/get_renewals.

Env:
  GITHUB_TOKEN   -- fine-grained PAT with Actions:write on this repo
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.error
from datetime import datetime


REPO = "ymi-flowing/sns-daily-report"
WORKFLOW_FILE = "sync-renewals.yml"


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
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            self._send(500, {"success": False, "error": "GITHUB_TOKEN not configured"})
            return
        url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        body = json.dumps({"ref": "main"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SNS-Renewals-Trigger")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            if resp.status == 204:
                self._send(200, {
                    "success": True,
                    "message": "Sync Renewals workflow dispatched",
                    "dispatchedAt": datetime.utcnow().isoformat() + "Z",
                })
            else:
                self._send(resp.status, {"success": False, "error": f"Unexpected status {resp.status}"})
        except urllib.error.HTTPError as e:
            self._send(e.code, {"success": False, "error": f"GitHub API {e.code}: {e.read().decode()}"})
        except Exception as e:
            self._send(500, {"success": False, "error": str(e)})
