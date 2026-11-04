"""
Vercel Serverless Function - Serve the SNS Data Analyst Skill markdown.

Endpoint: GET /api/skill

Returns the contents of skills/SNS Data Analyst Skill.md as text/markdown.
The file already has the publishable (read-only) Supabase key embedded,
so any AI model that fetches this URL gets a self-contained reference for
querying the property_snapshots database. No other data access, no write
credentials, no tool surface -- just the markdown.

Share: https://sns-daily-report.vercel.app/api/skill
"""
from http.server import BaseHTTPRequestHandler
import os


SKILL_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "SNS Data Analyst Skill.md",
))


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body, mime="text/markdown; charset=utf-8"):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", 'inline; filename="sns-data-analyst-skill.md"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=60")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            with open(SKILL_PATH, "r", encoding="utf-8") as f:
                self._send(200, f.read())
        except FileNotFoundError:
            self._send(404, "skill file not found", mime="text/plain")
        except Exception as exc:  # noqa: BLE001
            self._send(500, f"error: {exc}", mime="text/plain")
