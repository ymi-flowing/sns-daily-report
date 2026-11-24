"""
Fetch ResMan Partners API /Invoices for one property since a given datetime.

CLI:
    python api/get_invoices.py \
        --property 49thStr \
        --since 2026-08-01 \
        --out invoices.json \
        [--only-moved-out-since 2026-08-18] \
        [--callback https://n8n.example.com/webhook-waiting/abc]

Inputs:
- `--property`: either a property abbreviation (e.g. "49thStr") or a GUID.
- `--since`: ISO date (`YYYY-MM-DD`) or full datetime (`YYYY-MM-DD HH:MM:SS`).
- `--out`: file path for the JSON payload (also POSTed to callback if provided).
- `--only-moved-out-since`: optional YYYY-MM-DD. When set, pulls /Leasing/Leases,
  builds the set of units whose moveOutDate >= that date, and keeps only invoice
  line items with locationType="Unit" + locationId in that set.

Output payload shape:
{
  "property": {"id": "<guid>", "abbreviation": "49thStr", "name": "49th St Apartments"},
  "since": "2026-08-01 00:00:00",
  "fetched_at": "2026-11-18T15:30:00Z",
  "move_out_filter": null | {"since": "2026-08-18", "unit_count": <int>},
  "moved_out_units": [ ... | {unitId, unitNumber, moveInDate, moveOutDate, resident} ],
  "invoice_count": <int>,         # after filter if applied
  "line_item_count": <int>,       # after filter if applied
  "total_sum": <float>,           # after filter if applied
  "invoices": [ ... raw invoice objects, each with lineItems[] ... ]
}

Each lineItem carries `propertyId`, `propertyAbbreviation`, `locationType`
(`None` | `"Unit"` | `"Building"`), `locationId`, `location`, plus GL + amounts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

API_KEY = os.environ.get("RESMAN_API_KEY", "43XzRFJkhvAJGLvR6GlJUpKy6nyc")
PARTNER_ID = os.environ.get("RESMAN_PARTNER_ID", "SNSaPi")
ACCOUNT_ID = os.environ.get("RESMAN_ACCOUNT_ID", "1550")

LEGACY_API_URL = "https://api.myresman.com"
PARTNERS_API_URL = "https://partners-api.myresman.com"

TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 3
CALL_DELAY = 0.5


def legacy_post(endpoint: str, extra_body: dict | None = None) -> dict | None:
    body = {
        "IntegrationPartnerID": PARTNER_ID,
        "APIKey": API_KEY,
        "AccountID": ACCOUNT_ID,
    }
    if extra_body:
        body.update(extra_body)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{LEGACY_API_URL}/{endpoint}",
                json=body,
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            )
            if resp.ok:
                data = resp.json()
                if data.get("Status") not in ("Failed", "Error"):
                    return data
            print(f"[legacy] {endpoint} attempt {attempt}: HTTP {resp.status_code}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"[legacy] {endpoint} attempt {attempt}: {e}", file=sys.stderr)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    return None


def partners_get(endpoint: str, params: dict | None = None):
    headers = {"ResMan-Account-Id": ACCOUNT_ID, "Accept": "application/json"}
    time.sleep(CALL_DELAY)
    last_status = 0
    last_body = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                f"{PARTNERS_API_URL}{endpoint}",
                headers=headers,
                params=params,
                auth=HTTPBasicAuth(PARTNER_ID, API_KEY),
                timeout=TIMEOUT,
            )
            last_status = resp.status_code
            try:
                last_body = resp.json()
            except ValueError:
                last_body = resp.text
            if resp.ok:
                return last_status, last_body
            if resp.status_code < 500 and resp.status_code != 429:
                return last_status, last_body
            print(f"[partners] {endpoint} attempt {attempt}: HTTP {resp.status_code}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"[partners] {endpoint} attempt {attempt}: {e}", file=sys.stderr)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    return last_status, last_body


def is_guid(s: str) -> bool:
    return (
        isinstance(s, str)
        and len(s) == 36
        and s.count("-") == 4
    )


def resolve_property(property_arg: str) -> dict:
    """Map a user-supplied `property` input (abbrev or GUID) to {id, abbrev, name}."""
    data = legacy_post("Account/GetProperties")
    if not data:
        raise SystemExit("Failed to fetch Account/GetProperties")
    props = data.get("Properties") or []

    if is_guid(property_arg):
        for p in props:
            if (p.get("PropertyID") or "").lower() == property_arg.lower():
                return {
                    "id": p.get("PropertyID"),
                    "abbreviation": p.get("Abbreviation") or p.get("ShortName") or p.get("Name"),
                    "name": p.get("Name"),
                }
        raise SystemExit(f"GUID {property_arg} not found among {len(props)} properties.")

    # Treat as abbreviation. Try exact match first (case-insensitive), then fuzzy by Name.
    target = property_arg.strip().lower()
    for p in props:
        abbrev = (p.get("Abbreviation") or p.get("ShortName") or "").strip().lower()
        if abbrev == target:
            return {
                "id": p.get("PropertyID"),
                "abbreviation": p.get("Abbreviation") or p.get("ShortName"),
                "name": p.get("Name"),
            }
    for p in props:
        name = (p.get("Name") or "").strip().lower()
        if target in name:
            return {
                "id": p.get("PropertyID"),
                "abbreviation": p.get("Abbreviation") or p.get("ShortName"),
                "name": p.get("Name"),
            }

    available = ", ".join(sorted({(p.get("Abbreviation") or p.get("ShortName") or p.get("Name") or "") for p in props}))
    raise SystemExit(f"Property {property_arg!r} not matched. Available: {available}")


def fetch_moved_out_units(property_id: str, moved_out_since: str) -> list[dict]:
    """
    Pull /Leasing/Leases (with history) and return leases whose moveOutDate >= moved_out_since.

    The returned list is deduped per unitId, keeping the most recent moveOutDate per unit.
    Each row: {unitId, unitNumber, moveInDate, moveOutDate, resident}.
    """
    # Use a wider modifiedSince so we don't miss leases that moved out recently
    # but haven't been modified in the last few weeks.
    legacy_since = (datetime.strptime(moved_out_since, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    status, body = partners_get(
        "/Leasing/Leases",
        params={
            "propertyId": property_id,
            "includeLeaseHistory": "true",
            "modifiedSince": legacy_since,
        },
    )
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"/Leasing/Leases returned HTTP {status}")

    leases = body.get("leases") or []
    per_unit: dict[str, dict] = {}
    for lease in leases:
        mod = lease.get("moveOutDate")
        if not mod:
            continue
        # moveOutDate is "YYYY-MM-DD" (possibly with timestamp); compare the date portion.
        mod_date = mod[:10]
        if mod_date < moved_out_since:
            continue
        unit_id = lease.get("unitId")
        if not unit_id:
            continue

        people = lease.get("people") or []
        resident = None
        if isinstance(people, list) and people:
            p = people[0] if isinstance(people[0], dict) else {}
            first = (p.get("firstName") or "").strip()
            last = (p.get("lastName") or "").strip()
            resident = f"{first} {last}".strip() or p.get("fullName") or p.get("name")

        row = {
            "unitId": unit_id,
            "unitNumber": lease.get("unitNumber"),
            "moveInDate": (lease.get("moveInDate") or "")[:10] or None,
            "moveOutDate": mod_date,
            "resident": resident,
        }
        prior = per_unit.get(unit_id)
        if prior is None or (prior.get("moveOutDate") or "") < mod_date:
            per_unit[unit_id] = row

    return sorted(per_unit.values(), key=lambda r: r.get("moveOutDate") or "", reverse=True)


def filter_invoices_by_units(invoices: list[dict], unit_ids: set[str]) -> list[dict]:
    """
    Keep only invoices whose lineItems include a Unit match. Within each kept invoice,
    strip non-matching lineItems and recompute `filteredTotal`/`filteredLineItemCount`
    (leaving original `total` intact for audit).
    """
    kept: list[dict] = []
    for inv in invoices:
        matches = [
            li for li in (inv.get("lineItems") or [])
            if li.get("locationType") == "Unit" and li.get("locationId") in unit_ids
        ]
        if not matches:
            continue
        filtered_total = round(sum(float(li.get("total") or 0.0) for li in matches), 2)
        new_inv = dict(inv)
        new_inv["lineItems"] = matches
        new_inv["filteredLineItemCount"] = len(matches)
        new_inv["filteredTotal"] = filtered_total
        kept.append(new_inv)
    return kept


def default_window_since(now: datetime | None = None) -> str:
    """
    Return the first day of (current_month - 6) at 00:00:00.

    "Last 6 full months including the current" → in Nov 2026 the window starts
    on 2026-05-01; in Dec 2026 on 2026-06-01; in Jan 2027 on 2026-07-01.
    """
    now = now or datetime.now()
    year = now.year
    month = now.month - 6
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1).strftime("%Y-%m-%d %H:%M:%S")


def normalize_since(raw: str) -> str:
    """Return a `YYYY-MM-DD HH:MM:SS` string accepted by /Invoices."""
    raw = raw.strip()
    # Already a full datetime?
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    # Date-only
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d 00:00:00")
    except ValueError:
        raise SystemExit(f"Unparseable --since value: {raw!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--property", required=True, help="Property abbreviation (e.g. 49thStr) or GUID.")
    parser.add_argument(
        "--since",
        default=None,
        help="YYYY-MM-DD or YYYY-MM-DD HH:MM:SS. Defaults to first day of (current month - 6).",
    )
    parser.add_argument("--out", default="invoices.json", help="Output JSON path.")
    parser.add_argument(
        "--only-moved-out-since",
        default=None,
        dest="only_moved_out_since",
        help="YYYY-MM-DD. If set, keep only line items for units that moved out on/after this date.",
    )
    parser.add_argument("--callback", default=None, help="Optional n8n resumeUrl to POST the result to.")
    args = parser.parse_args()

    if args.only_moved_out_since:
        try:
            datetime.strptime(args.only_moved_out_since, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"--only-moved-out-since must be YYYY-MM-DD, got {args.only_moved_out_since!r}")

    prop = resolve_property(args.property)
    since = normalize_since(args.since) if args.since else default_window_since()
    print(f"Property: {prop['abbreviation']!r} / {prop['name']!r} / {prop['id']}")
    print(f"modifiedSince: {since}")

    status, body = partners_get(
        "/Invoices",
        params={"propertyId": prop["id"], "modifiedSince": since},
    )
    if status != 200:
        print(f"ERROR: /Invoices returned HTTP {status}", file=sys.stderr)
        print(json.dumps(body, indent=2, default=str)[:2000], file=sys.stderr)
        return 2

    if isinstance(body, dict):
        invoices = body.get("invoices") or []
    elif isinstance(body, list):
        invoices = body
    else:
        invoices = []

    move_out_filter = None
    moved_out_units: list[dict] = []
    if args.only_moved_out_since:
        print(f"Resolving units moved out since {args.only_moved_out_since}...")
        moved_out_units = fetch_moved_out_units(prop["id"], args.only_moved_out_since)
        unit_ids = {u["unitId"] for u in moved_out_units if u.get("unitId")}
        print(f"  {len(moved_out_units)} units moved out in the window.")
        invoices = filter_invoices_by_units(invoices, unit_ids)
        print(f"  {len(invoices)} invoices retained after unit filter.")
        move_out_filter = {
            "since": args.only_moved_out_since,
            "unit_count": len(moved_out_units),
        }

    line_count = 0
    total_sum = 0.0
    for inv in invoices:
        for li in inv.get("lineItems") or []:
            line_count += 1
            total_sum += float(li.get("total") or 0.0)

    payload = {
        "property": prop,
        "since": since,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "move_out_filter": move_out_filter,
        "moved_out_units": moved_out_units,
        "invoice_count": len(invoices),
        "line_item_count": line_count,
        "total_sum": round(total_sum, 2),
        "invoices": invoices,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out_path} (invoices={len(invoices)} lineItems={line_count} total=${total_sum:,.2f})")

    if args.callback:
        print(f"POSTing payload to callback: {args.callback}")
        try:
            cb = requests.post(
                args.callback,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60,
            )
            print(f"Callback HTTP {cb.status_code}")
            if not cb.ok:
                print(cb.text[:500], file=sys.stderr)
                return 3
        except requests.RequestException as e:
            print(f"Callback failed: {e}", file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
