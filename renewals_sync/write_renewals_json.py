"""
Fetch all 10 property tabs from the Renewal Letters Google Sheet and write
a single `renewals_data.json` file at the repo root. Also augments each row
with two LIVE fields that do NOT live in the sheet: `NTV Date` and
`Move Out Date` pulled from the ResMan Partners API via Lease ID.

Env:
  SHEETS_WEBHOOK_URL -- Apps Script webhook
  SHARED_SECRET      -- Apps Script shared secret
  RESMAN_API_KEY     -- ResMan Partners API key
  RESMAN_PARTNER_ID  -- ResMan integration partner id
  RESMAN_ACCOUNT_ID  -- ResMan account id (1550 for SNS)

Output file layout:
  {
    "generatedAt": "<ISO UTC>",
    "tabs": {
      "49thStr":  { "headers": [..., "NTV Date", "Move Out Date"], "rows": [[...], ...] },
      ...
    }
  }
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse shared code from the sync pipeline.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sheets_client
from create_monday_items import fetch_leases, get_property_id
from sync_renewal_status import find_expiring_and_renewal, lease_has_ended, parse_iso_date


PROPERTY_TABS = [
    "49thStr", "ORT", "AP", "TV", "MOC", "MUSO",
    "SJ", "ROP", "EAST", "TRA-AN3",
]

# Which ResMan property abbreviation(s) feed each sheet tab.
TAB_TO_PROPERTIES = {
    "49thStr":  ["49thStr"],
    "ORT":      ["ORT"],
    "AP":       ["AP"],
    "TV":       ["TV"],
    "MOC":      ["MOC"],
    "MUSO":     ["MUSO"],
    "SJ":       ["SJ"],
    "ROP":      ["ROP"],
    "EAST":     ["EAST"],
    "TRA-AN3":  ["TRA", "AN3"],
}

EXTRA_HEADERS = ["NTV Date", "Move Out Date", "Lease End Date", "Renewal Signed Date"]

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


def build_resman_lookup(property_abbrs: list) -> dict:
    """
    Fetch leases from each ResMan property and return
      { leaseId: full_lease_dict }
    so callers can derive dates lazily (lease end, renewal signed, etc).
    """
    out = {}
    for abbr in property_abbrs:
        pid = get_property_id(abbr)
        if not pid:
            print(f"  WARNING: could not resolve property id for {abbr}")
            continue
        leases = fetch_leases(pid)
        print(f"  {abbr}: {len(leases)} leases")
        for L in leases:
            lid = L.get("leaseId")
            if lid:
                out[lid] = L
    return out


def _parse_exp_month(cell) -> tuple | None:
    """Column A value -> (year, month) or None. Handles ISO and 'YYYY-MM'."""
    if not cell:
        return None
    s = str(cell).strip()
    try:
        return int(s[:4]), int(s[5:7])
    except (ValueError, IndexError):
        return None


def _renewal_signed_date(lease: dict, exp_cell) -> str:
    """Signed date of the best-matching renewal entry for the row's expiring month."""
    if not lease or lease_has_ended(lease):
        return ""
    ym = _parse_exp_month(exp_cell)
    if not ym:
        return ""
    _, renewal_entry = find_expiring_and_renewal(lease, ym[0], ym[1])
    if not renewal_entry:
        return ""
    sd = renewal_entry.get("signedDate") or ""
    return sd[:10]


def augment_rows(tab_data: dict, lease_lookup: dict) -> dict:
    """Append NTV Date + Move Out Date + Lease End Date + Renewal Signed Date."""
    headers = tab_data["headers"]
    blanks = [""] * len(EXTRA_HEADERS)
    if "Lease ID" not in headers:
        print(f"  WARNING: 'Lease ID' header missing; skipping ResMan merge")
        return {
            "headers": headers + EXTRA_HEADERS,
            "rows": [r + blanks for r in tab_data["rows"]],
        }
    lid_idx = headers.index("Lease ID")
    exp_idx = headers.index("Expiring Month") if "Expiring Month" in headers else 0
    new_rows = []
    hits = 0
    for r in tab_data["rows"]:
        lid = str(r[lid_idx]).strip() if lid_idx < len(r) else ""
        lease = lease_lookup.get(lid) if lid else None
        if lease:
            ntv = (lease.get("noticeToVacateDate") or "")[:10]
            # Fall back to scheduledMoveOutDate so NTV + MTM rows show the
            # planned move-out, not a blank. Actual moveOutDate still wins
            # once the resident has physically left.
            mo = (lease.get("moveOutDate") or lease.get("scheduledMoveOutDate") or "")[:10]
            lease_end = (lease.get("leaseEndDate") or "")[:10]
            signed = _renewal_signed_date(lease, r[exp_idx] if exp_idx < len(r) else "")
            new_rows.append(r + [ntv, mo, lease_end, signed])
            hits += 1
        else:
            new_rows.append(r + blanks)
    print(f"  augmented {hits}/{len(new_rows)} rows with ResMan dates")
    return {
        "headers": headers + EXTRA_HEADERS,
        "rows": new_rows,
    }


def main() -> int:
    if not os.environ.get("SHEETS_WEBHOOK_URL") or not os.environ.get("SHARED_SECRET"):
        print("ERROR: SHEETS_WEBHOOK_URL or SHARED_SECRET env var missing.")
        return 2

    print(f"Fetching {len(PROPERTY_TABS)} tabs + ResMan dates...")
    tabs = {}
    total_rows = 0
    for tab in PROPERTY_TABS:
        print(f"\n[{tab}]")
        try:
            data = fetch_tab(tab)
            n = len(data["rows"])
            print(f"  sheet: headers={len(data['headers'])} rows={n}")
            resman_props = TAB_TO_PROPERTIES.get(tab, [tab])
            lease_lookup = build_resman_lookup(resman_props)
            tabs[tab] = augment_rows(data, lease_lookup)
            total_rows += n
        except Exception as e:
            print(f"  ERROR: {e}")
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
