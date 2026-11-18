"""
Pull ResMan /Invoices + move-out units for EVERY SNS property and write
a single merged `invoices_data.json` for the Invoices.html dashboard.

Window: first day of (current_month - 3) at 00:00:00 -> today.
    Nov 2026 -> 2026-08-01
    Dec 2026 -> 2026-09-01
    Jan 2027 -> 2026-10-01

Output shape:
{
  "generatedAt": "2026-11-18T15:30:00Z",
  "sinceDate": "2026-08-01",
  "properties": [{"id": "...", "abbreviation": "49thStr", "name": "..."}],
  "invoices": [
    { ...raw invoice fields,
      "propertyAbbreviation": "49thStr",
      "propertyName": "49th St Apartments",
      "lineItems": [ ... ],  # untouched; locationType / locationId / location preserved
    }
  ],
  "moved_out_units": [
    {"propertyAbbreviation": "49thStr", "unitId": "...", "unitNumber": "1515",
     "moveInDate": "2024-10-02", "moveOutDate": "2026-10-01", "resident": "..."}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# api/get_invoices.py sits at <repo>/api/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from api.get_invoices import (  # noqa: E402
    default_window_since,
    fetch_moved_out_units,
    legacy_post,
    partners_get,
)


GL_ACCOUNTS_PATH = Path(__file__).resolve().parent / "gl_accounts.json"


def load_gl_mapping() -> tuple[dict, dict]:
    try:
        data = json.loads(GL_ACCOUNTS_PATH.read_text(encoding="utf-8"))
        return data.get("codes") or {}, data.get("prefixes") or {}
    except Exception:
        return {}, {}


def fetch_invoices_for_property(property_id: str, since: str) -> list[dict]:
    status, body = partners_get(
        "/Invoices",
        params={"propertyId": property_id, "modifiedSince": since},
    )
    if status != 200:
        print(f"  WARN: /Invoices returned HTTP {status}", file=sys.stderr)
        return []
    if isinstance(body, dict):
        return body.get("invoices") or []
    if isinstance(body, list):
        return body
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="invoices_data.json", help="Output JSON path.")
    parser.add_argument(
        "--since",
        default=None,
        help="Override default window start. YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.",
    )
    args = parser.parse_args()

    since = args.since or default_window_since()
    since_date = since[:10]  # YYYY-MM-DD portion (used for /Leasing/Leases and reporting)
    print(f"Window: since = {since}")

    props_data = legacy_post("Account/GetProperties")
    if not props_data:
        raise SystemExit("Failed to call Account/GetProperties")
    raw_props = props_data.get("Properties") or []

    # Keep only the 9 active SNS properties if labeled; otherwise everything returned.
    properties = []
    for p in raw_props:
        pid = p.get("PropertyID")
        abbrev = p.get("Abbreviation") or p.get("ShortName") or p.get("Name")
        name = p.get("Name")
        if not (pid and abbrev):
            continue
        properties.append({"id": pid, "abbreviation": abbrev, "name": name})

    # Stable order: alphabetical by abbreviation.
    properties.sort(key=lambda p: (p["abbreviation"] or "").lower())
    print(f"Found {len(properties)} properties")

    all_invoices: list[dict] = []
    all_moved_out: list[dict] = []

    for i, prop in enumerate(properties, 1):
        label = f"[{i}/{len(properties)}] {prop['abbreviation']}"
        print(f"{label}: fetching invoices...")
        invs = fetch_invoices_for_property(prop["id"], since)
        for inv in invs:
            inv["propertyAbbreviation"] = prop["abbreviation"]
            inv["propertyName"] = prop["name"]
        all_invoices.extend(invs)
        print(f"  {len(invs)} invoices")

        print(f"{label}: fetching moved-out units since {since_date}...")
        try:
            mo_units = fetch_moved_out_units(prop["id"], since_date)
        except SystemExit as e:
            print(f"  WARN: {e}", file=sys.stderr)
            mo_units = []
        for u in mo_units:
            u["propertyAbbreviation"] = prop["abbreviation"]
        all_moved_out.extend(mo_units)
        print(f"  {len(mo_units)} moved-out units")

    # The API filters by modifiedSince, not accountingDate — an ancient invoice
    # that was re-touched recently will leak through. Drop anything whose
    # accountingDate falls outside our expense window.
    before = len(all_invoices)
    all_invoices = [
        inv for inv in all_invoices
        if ((inv.get("accountingDate") or "")[:10] or since_date) >= since_date
    ]
    print(f"After accountingDate filter: {len(all_invoices)} invoices (dropped {before - len(all_invoices)})")

    # Drop properties that returned no activity in the window (retired/archived).
    active_abbrevs = {inv.get("propertyAbbreviation") for inv in all_invoices}
    active_abbrevs.update(u.get("propertyAbbreviation") for u in all_moved_out)
    properties = [p for p in properties if p["abbreviation"] in active_abbrevs]
    print(f"Active properties with data: {len(properties)}")

    # Stamp GL account descriptions (and prefix labels) onto each line item.
    gl_codes, gl_prefixes = load_gl_mapping()
    for inv in all_invoices:
        for li in inv.get("lineItems") or []:
            code = (li.get("glAccountNumber") or "").strip()
            if not code:
                continue
            desc = gl_codes.get(code)
            if desc:
                li["glDescription"] = desc
            prefix = code.split("-")[0] if "-" in code else code
            li["glPrefix"] = prefix
            li["glPrefixLabel"] = gl_prefixes.get(prefix) or prefix

    total_lines = sum(len(inv.get("lineItems") or []) for inv in all_invoices)
    total_sum = round(
        sum(float(li.get("total") or 0.0) for inv in all_invoices for li in (inv.get("lineItems") or [])),
        2,
    )

    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sinceDate": since_date,
        "properties": properties,
        "invoice_count": len(all_invoices),
        "line_item_count": total_lines,
        "moved_out_unit_count": len(all_moved_out),
        "total_sum": total_sum,
        "gl_accounts": {"codes": gl_codes, "prefixes": gl_prefixes},
        "invoices": all_invoices,
        "moved_out_units": all_moved_out,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print()
    print(f"Wrote {out_path} ({size_kb:,.1f} KB)")
    print(
        f"  invoices={len(all_invoices)}  lineItems={total_lines}  "
        f"moved_out_units={len(all_moved_out)}  total=${total_sum:,.2f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
