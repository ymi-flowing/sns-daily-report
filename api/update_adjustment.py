"""
Vercel Serverless Function - Update Adjustment (column N) for one sheet row.

POSTs from the Offers popup's inline Adjustment editor land here. We identify
the sheet row by (tab, Unit, Expiring Month) and write the new value to
cell N{row} via the Apps Script webhook's set_range action. The sheet's own
formulas then recompute Rent A (P), Total Offer A (Q), Delta A (R), Delta A %
(S) on the next read.

Request:
  POST /api/update_adjustment
  Content-Type: application/json
  Body: {
    "tab":        "<tab name, e.g. 49thStr or TRA-AN3>",
    "unit":       "<unit label from column B>",
    "expMonth":   "<Expiring Month cell from column A, raw>",
    "adjustment": <number>,
    "password":   "TaliRenewals123457$"
  }

Response:
  200 { "ok": true,  "cell": "N15", "tab": "...", "value": 123 }
  400/403/404/409/500/502 { "error": "..." }

Env:
  SHEETS_WEBHOOK_URL (or SHEET_WEBHOOK_URL)
  SHARED_SECRET
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import re
import urllib.request


SHEETS_WEBHOOK_URL = (
    os.environ.get("SHEETS_WEBHOOK_URL", "")
    or os.environ.get("SHEET_WEBHOOK_URL", "")
)
SHARED_SECRET = os.environ.get("SHARED_SECRET", "")

EDIT_PASSWORD = "TaliRenewals123457$"
RANGE_PER_TAB = "A1:AJ400"


def _post_webhook(payload: dict, timeout: int = 45) -> dict:
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


def _norm_month(cell) -> str:
    """Normalize column-A values to 'YYYY-MM'. Handles ISO datetimes + 'YYYY-M'."""
    s = str(cell or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return s


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
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return self._send_json(400, {"error": "invalid JSON body"})

        if body.get("password") != EDIT_PASSWORD:
            return self._send_json(403, {"error": "incorrect password"})

        tab = (body.get("tab") or "").strip()
        unit = str(body.get("unit") or "").strip()
        exp_month = _norm_month(body.get("expMonth"))
        try:
            adjustment = float(body.get("adjustment"))
        except (TypeError, ValueError):
            return self._send_json(400, {"error": "adjustment must be a number"})

        if not tab or not unit or not exp_month:
            return self._send_json(400, {"error": "missing tab, unit, or expMonth"})
        if not SHEETS_WEBHOOK_URL or not SHARED_SECRET:
            return self._send_json(500, {"error": "SHEETS_WEBHOOK_URL or SHARED_SECRET missing"})

        try:
            data = _post_webhook({
                "action": "get_range",
                "tab": tab,
                "range": RANGE_PER_TAB,
                "secret": SHARED_SECRET,
            })
        except Exception as e:
            return self._send_json(502, {"error": f"sheet read failed: {e}"})

        values = data.get("values") or []
        if not values:
            return self._send_json(404, {"error": f"tab {tab!r} empty"})
        headers = values[0]
        try:
            i_unit = headers.index("Unit")
            i_month = headers.index("Expiring Month")
        except ValueError:
            return self._send_json(500, {"error": "sheet missing Unit or Expiring Month header"})

        matches = []
        # values[0] is the header row (sheet row 1); data rows start at sheet row 2.
        for sheet_row, r in enumerate(values[1:], start=2):
            if len(r) <= max(i_unit, i_month):
                continue
            if str(r[i_unit]).strip() != unit:
                continue
            if _norm_month(r[i_month]) != exp_month:
                continue
            matches.append(sheet_row)

        if len(matches) == 0:
            return self._send_json(404, {
                "error": f"no row for unit {unit!r} / {exp_month!r} in tab {tab!r}"
            })
        if len(matches) > 1:
            return self._send_json(409, {
                "error": f"ambiguous: {len(matches)} rows match unit {unit!r} / {exp_month!r}"
            })

        sheet_row = matches[0]
        cell = f"N{sheet_row}"
        try:
            res = _post_webhook({
                "action": "set_range",
                "tab": tab,
                "range": cell,
                "values": [[adjustment]],
                "mode": "values",
                "secret": SHARED_SECRET,
            })
        except Exception as e:
            return self._send_json(502, {"error": f"sheet write failed: {e}"})

        if res.get("ok") is False:
            return self._send_json(502, {"error": res.get("error") or "unknown sheet error"})

        return self._send_json(200, {
            "ok": True,
            "tab": tab,
            "cell": cell,
            "value": adjustment,
        })
