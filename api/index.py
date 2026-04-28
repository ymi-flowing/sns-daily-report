"""
Vercel Serverless Function - Trigger GitHub Actions workflow
Endpoint: POST /api/refresh
"""
from http.server import BaseHTTPRequestHandler
import os
import json
import urllib.request
import urllib.error


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        # Get GitHub token from environment
        github_token = os.environ.get("GITHUB_TOKEN")

        if not github_token:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "GitHub token not configured"}).encode())
            return

        try:
            # Trigger GitHub Actions workflow
            url = "https://api.github.com/repos/ymi-flowing/sns-daily-report/actions/workflows/refresh.yml/dispatches"
            data = json.dumps({"ref": "main"}).encode('utf-8')

            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header('Authorization', f'Bearer {github_token}')
            req.add_header('Accept', 'application/vnd.github.v3+json')
            req.add_header('User-Agent', 'SNS-Dashboard-Refresh')
            req.add_header('Content-Type', 'application/json')

            try:
                response = urllib.request.urlopen(req)
                status = response.status
            except urllib.error.HTTPError as e:
                status = e.code
                error_body = e.read().decode()

                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": False,
                    "error": f"GitHub API returned {status}: {error_body}"
                }).encode())
                return

            if status == 204:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "message": "Refresh workflow triggered successfully"
                }).encode())
            else:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": False,
                    "error": f"GitHub API returned {status}"
                }).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": False,
                "error": str(e)
            }).encode())
