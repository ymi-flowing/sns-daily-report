"""
Vercel Serverless Function - Batch-update Adjustment (column N) for many
sheet rows in a single request.

The Offers popup collects edits locally while in edit mode. When the user
clicks Save, the whole batch comes here. We group by tab, read each affected
tab's A1:AJ400 ONCE, then write each individual N{row} cell via set_range.
This keeps individual edits instant on the client (no network per keystroke)
and shrinks the sheet round-trips to one read per tab + one write per edit.

Returned results preserve input order (results[i] corresponds to edits[i]) so
the client can match failures back to its pending map.

Request:
  POST /api/update_adjustments_batch
  Content-Type: application/json
  Body: {
    "password": "TaliRenewals123457$",
    "edits": [
      {"tab":"49thStr","unit":"1015","expMonth":"2026-11","adjustment":-150},
      ...
    ]
  }

Response:
  200 { "ok": true|false, "results": [ {"tab":..,"unit":..,"expMonth":..,"ok":true,"cell":"N17"}
                                     | {"tab":..,"unit":..,"expMonth":..,"ok":false,"error":"..."}, ... ] }

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

        edits = body.get("edits")
        if not isinstance(edits, list) or not edits:
            return self._send_json(400, {"error": "edits must be a non-empty list"})
        if not SHEETS_WEBHOOK_URL or not SHARED_SECRET:
            return self._send_json(500, {"error": "SHEETS_WEBHOOK_URL or SHARED_SECRET missing"})

        # Normalise + validate input into index-preserving records.
        normed = []
        for e in edits:
            tab = (e.get("tab") or "").strip()
            unit = str(e.get("unit") or "").strip()
            exp_month = _norm_month(e.get("expMonth"))
            try:
                adj = float(e.get("adjustment"))
            except (TypeError, ValueError):
                adj = None
            normed.append({
                "tab": tab,
                "unit": unit,
                "expMonth": exp_month,
                "adjustment": adj,
            })

        # For each unique tab, do ONE get_range and build a (unit,month)->row lookup.
        tab_lookups = {}
        tab_errors = {}
        unique_tabs = {n["tab"] for n in normed if n["tab"]}
        for tab in unique_tabs:
            try:
                data = _post_webhook({
                    "action": "get_range",
                    "tab": tab,
                    "range": RANGE_PER_TAB,
                    "secret": SHARED_SECRET,
                })
            except Exception as ex:
                tab_errors[tab] = f"sheet read failed: {ex}"
                continue
            values = data.get("values") or []
            if not values:
                tab_errors[tab] = f"tab {tab!r} empty"
                continue
            headers = values[0]
            try:
                i_unit = headers.index("Unit")
                i_month = headers.index("Expiring Month")
            except ValueError:
                tab_errors[tab] = "sheet missing Unit or Expiring Month header"
                continue
            lookup = {}
            for sheet_row, r in enumerate(values[1:], start=2):
                if len(r) <= max(i_unit, i_month):
                    continue
                u = str(r[i_unit]).strip()
                m = _norm_month(r[i_month])
                lookup.setdefault((u, m), []).append(sheet_row)
            tab_lookups[tab] = lookup

        results = []
        for n in normed:
            tab, unit, exp_month, adj = n["tab"], n["unit"], n["expMonth"], n["adjustment"]
            base = {"tab": tab, "unit": unit, "expMonth": exp_month}
            if not tab or not unit or not exp_month:
                results.append({**base, "ok": False, "error": "missing tab/unit/expMonth"})
                continue
            if adj is None:
                results.append({**base, "ok": False, "error": "adjustment must be a number"})
                continue
            if tab in tab_errors:
                results.append({**base, "ok": False, "error": tab_errors[tab]})
                continue
            matches = tab_lookups.get(tab, {}).get((unit, exp_month), [])
            if not matches:
                results.append({**base, "ok": False, "error": f"no row for unit {unit!r} / {exp_month!r}"})
                continue
            if len(matches) > 1:
                results.append({**base, "ok": False, "error": f"ambiguous: {len(matches)} rows"})
                continue
            sheet_row = matches[0]
            cell = f"N{sheet_row}"
            try:
                _post_webhook({
                    "action": "set_range",
                    "tab": tab,
                    "range": cell,
                    "values": [[adj]],
                    "mode": "values",
                    "secret": SHARED_SECRET,
                })
                results.append({**base, "ok": True, "cell": cell})
            except Exception as ex:
                results.append({**base, "ok": False, "error": f"sheet write failed: {ex}"})

        all_ok = all(r.get("ok") for r in results)
        self._send_json(200, {"ok": all_ok, "results": results})
