"""
Vercel Serverless Function - Get the timestamp of the last successful
ResMan sync (sync-renewals.yml workflow completion).

Endpoint: GET /api/last_sync

Returns:
  {
    "lastSuccessfulRun": "<ISO>" | null,
    "htmlUrl": "<link>" | null,
  }

Used by the Renewals Dashboard to show when ResMan data was last pulled
into the Google Sheet (NOT when the browser last fetched the sheet).

Env:
  GITHUB_TOKEN  -- fine-grained PAT with Actions:read on this repo
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.error


REPO = "ymi-flowing/sns-daily-report"
WORKFLOW_FILE = "sync-renewals.yml"


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        # Light cache so page loads don't hammer the GH API.
        self.send_header("Cache-Control", "public, max-age=30")
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
            self._send(500, {"error": "GITHUB_TOKEN not configured"})
            return
        # Query only completed+successful runs, newest first.
        url = (
            f"https://api.github.com/repos/{REPO}/actions/workflows/"
            f"{WORKFLOW_FILE}/runs?status=success&per_page=1"
        )
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SNS-Renewals-LastSync")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            runs = data.get("workflow_runs", [])
            if not runs:
                self._send(200, {"lastSuccessfulRun": None, "htmlUrl": None})
                return
            run = runs[0]
            self._send(200, {
                "lastSuccessfulRun": run.get("updated_at"),
                "htmlUrl": run.get("html_url"),
            })
        except urllib.error.HTTPError as e:
            self._send(e.code, {"error": f"GitHub API {e.code}: {e.read().decode()}"})
        except Exception as e:
            self._send(500, {"error": str(e)})
