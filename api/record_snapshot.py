"""
Vercel Serverless Function - Record today's SNS snapshot into Supabase.

Endpoint: POST /api/record_snapshot

Pulls the current data.json from the repo (raw.githubusercontent), builds the
9 property rows for today's ET date, checks whether that date is already in
property_snapshots, and either inserts or short-circuits.

Response (JSON):
    {
      "status": "success" | "already_recorded" | "error",
      "snapshot_date": "YYYY-MM-DD",
      "generated_at": "...",
      "inserted": 9,              // when status == success
      "existing_count": 9,        // when status == already_recorded
      "error": "..."              // when status == error
    }

Env:
  SUPABASE_URL              -- https://<project>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY -- secret API key
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

# Make project root importable so we can reuse the Python mapper.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from supabase_sync import (
    property_snapshot_from_data_json,
    _normalize_batch,
    date_already_recorded,
    TABLE,
)
from export_excel import ALL_PROPS

DATA_JSON_URL = "https://raw.githubusercontent.com/ymi-flowing/sns-daily-report/main/data.json"
FL_TZ = ZoneInfo("America/New_York")


def _fetch_data_json():
    req = urllib.request.Request(DATA_JSON_URL, headers={"User-Agent": "SNS-RecordSnapshot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _snapshot_date_from_data(data):
    ts = data.get("generatedAt")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone(FL_TZ).date()
        except (TypeError, ValueError):
            pass
    return datetime.now(FL_TZ).date()


def _supabase_post(url, key, rows):
    base = url.rstrip("/")
    if base.endswith("/rest/v1"):
        base = base[: -len("/rest/v1")]
    endpoint = f"{base}/rest/v1/{TABLE}?on_conflict=property_abbr,snapshot_date"
    body = json.dumps(_normalize_batch(rows), default=str).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "resolution=ignore-duplicates,return=minimal")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


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
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            self._send(500, {
                "status": "error",
                "error": "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured on Vercel.",
            })
            return

        try:
            data = _fetch_data_json()
        except Exception as exc:  # noqa: BLE001
            self._send(502, {
                "status": "error",
                "error": f"Failed to fetch data.json: {exc}",
            })
            return

        snapshot_date = _snapshot_date_from_data(data)
        generated_at = data.get("generatedAt")

        try:
            if date_already_recorded(url, key, snapshot_date):
                self._send(200, {
                    "status": "already_recorded",
                    "snapshot_date": snapshot_date.isoformat(),
                    "generated_at": generated_at,
                    "existing_count": 9,
                    "message": f"Snapshot for {snapshot_date} already in Supabase. No action taken.",
                })
                return

            rows = [
                property_snapshot_from_data_json(data, abbr, snapshot_date, "live")
                for abbr in ALL_PROPS
            ]
            _supabase_post(url, key, rows)

            self._send(200, {
                "status": "success",
                "snapshot_date": snapshot_date.isoformat(),
                "generated_at": generated_at,
                "inserted": len(rows),
                "message": f"Inserted {len(rows)} rows for {snapshot_date}.",
            })
        except urllib.error.HTTPError as exc:
            self._send(502, {
                "status": "error",
                "snapshot_date": snapshot_date.isoformat(),
                "error": f"Supabase HTTP {exc.code}: {exc.read().decode()[:500]}",
            })
        except Exception as exc:  # noqa: BLE001
            self._send(500, {
                "status": "error",
                "snapshot_date": snapshot_date.isoformat(),
                "error": str(exc),
            })
