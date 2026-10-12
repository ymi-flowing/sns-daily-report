"""
Fetch all 10 property tabs from the Renewal Letters Google Sheet and write
a single `renewals_data.json` file at the repo root.

The JSON is baked into the repo (committed by the sync workflow), so the
dashboard can load it as a static file -- same pattern as `generate_dashboard.py`
baking `dashboard_live.html`.

Env:
  SHEETS_WEBHOOK_URL -- Apps Script webhook (same as Renewal Letters/.env)
  SHARED_SECRET      -- Apps Script shared secret

Output file layout:
  {
    "generatedAt": "<ISO UTC>",
    "tabs": {
      "49thStr":  { "headers": [...], "rows": [[...], ...] },
      "ORT":      { "headers": [...], "rows": [[...], ...] },
      ...
    }
  }
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse the shared sheets client from the sync pipeline.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sheets_client


PROPERTY_TABS = [
    "49thStr", "ORT", "AP", "TV", "MOC", "MUSO",
    "SJ", "ROP", "EAST", "TRA-AN3",
]

RANGE_PER_TAB = "A1:AJ400"

# Repo root (parent of renewals_sync/)
REPO_ROOT = HERE.parent
OUTPUT = REPO_ROOT / "renewals_data.json"


def fetch_tab(tab: str) -> dict:
    data = sheets_client.get_range(tab, RANGE_PER_TAB)
    values = data.get("values", [])
    if not values:
        return {"headers": [], "rows": []}
    headers = values[0]
    rows = []
    for r in values[1:]:
        # Skip empty rows (both Expiring Month and Unit empty)
        if len(r) < 2 or (str(r[0]).strip() == "" and str(r[1]).strip() == ""):
            continue
        if len(r) < len(headers):
            r = list(r) + [""] * (len(headers) - len(r))
        rows.append(r)
    return {"headers": headers, "rows": rows}


def main() -> int:
    if not os.environ.get("SHEETS_WEBHOOK_URL") or not os.environ.get("SHARED_SECRET"):
        print("ERROR: SHEETS_WEBHOOK_URL or SHARED_SECRET env var missing.")
        return 2

    print(f"Fetching {len(PROPERTY_TABS)} tabs...")
    tabs = {}
    total_rows = 0
    for tab in PROPERTY_TABS:
        try:
            data = fetch_tab(tab)
            tabs[tab] = data
            n = len(data["rows"])
            total_rows += n
            print(f"  {tab:<10} headers={len(data['headers'])} rows={n}")
        except Exception as e:
            print(f"  {tab:<10} ERROR: {e}")
            tabs[tab] = {"headers": [], "rows": []}

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tabs": tabs,
    }

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"\nWrote {OUTPUT.name} ({size_kb:.1f} KB, {total_rows} total rows).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
