"""
Vercel Serverless Function - Get Renewal Status data for all properties.

Fetches every property tab from the Renewal Letters Google Sheet via its
Apps Script webhook. Returns a unified JSON payload:

  {
    "generatedAt": "<ISO timestamp>",
    "tabs": {
      "49thStr": { "headers": [...], "rows": [[...], ...] },
      "ORT":     { "headers": [...], "rows": [[...], ...] },
      ...
    }
  }

Env:
  SHEETS_WEBHOOK_URL   -- Apps Script /exec URL (same as Renewal Letters/.env)
  SHARED_SECRET        -- Apps Script shared secret

Endpoint:
  GET /api/get_renewals
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.error
from datetime import datetime


# Accept either name — SHEETS_* is the canonical name used in Renewal Letters/.env,
# SHEET_* (singular) is a tolerated alias in case it gets re-typed.
SHEETS_WEBHOOK_URL = (
    os.environ.get("SHEETS_WEBHOOK_URL", "")
    or os.environ.get("SHEET_WEBHOOK_URL", "")
)
SHARED_SECRET = os.environ.get("SHARED_SECRET", "")

# Property tab names in the Renewal Letters sheet. Order mirrors sync_all.py.
PROPERTY_TABS = [
    "49thStr", "ORT", "AP", "TV", "MOC", "MUSO",
    "SJ", "ROP", "EAST",
]

# Max rows per tab. The sheet caps at ~250 rows per property historically.
# A1:AJ400 is a safe range that covers all 36 columns + plenty of growth.
RANGE_PER_TAB = "A1:AJ400"


def _post_webhook(payload: dict, timeout: int = 45):
    """POST to Apps Script webhook. Returns parsed JSON or raises."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SHEETS_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    if not parsed.get("ok", True) and parsed.get("error"):
        raise RuntimeError(parsed["error"])
    return parsed


def fetch_tab(tab: str) -> dict:
    """
    Fetch one tab's headers + rows. Separates row 1 (headers) from the rest.
    Trims trailing empty rows (where unit cell is blank).
    """
    data = _post_webhook({
        "action": "get_range",
        "tab": tab,
        "range": RANGE_PER_TAB,
        "secret": SHARED_SECRET,
    })
    values = data.get("values", [])
    if not values:
        return {"headers": [], "rows": []}
    headers = values[0]
    rows = []
    for r in values[1:]:
        # Skip empty rows (no unit)
        if len(r) < 2 or (str(r[1]).strip() == "" and str(r[0]).strip() == ""):
            continue
        # Pad to full header width so client indexing is predictable
        if len(r) < len(headers):
            r = list(r) + [""] * (len(headers) - len(r))
        rows.append(r)
    return {"headers": headers, "rows": rows}


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, body: dict):
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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if not SHEETS_WEBHOOK_URL or not SHARED_SECRET:
            # Diagnostic: show which likely-related vars Vercel is passing in.
            # Values are NEVER returned, only names + lengths.
            similar = {}
            for name, value in os.environ.items():
                if any(k in name.upper() for k in ("SHEET", "SECRET", "RESMAN", "WEBHOOK")):
                    similar[name] = f"len={len(value)}"
            self._send_json(500, {
                "error": "Missing SHEETS_WEBHOOK_URL or SHARED_SECRET env vars",
                "diagnostic": {
                    "sheets_webhook_url_present": bool(SHEETS_WEBHOOK_URL),
                    "shared_secret_present": bool(SHARED_SECRET),
                    "related_env_vars_seen": similar,
                    "total_env_var_count": len(os.environ),
                },
            })
            return

        tabs_data = {}
        errors = {}
        for tab in PROPERTY_TABS:
            try:
                tabs_data[tab] = fetch_tab(tab)
            except Exception as e:
                errors[tab] = str(e)
                tabs_data[tab] = {"headers": [], "rows": []}

        self._send_json(200, {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "tabs": tabs_data,
            "errors": errors if errors else None,
        })
