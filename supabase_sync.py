"""
Upsert one row per property into Supabase `property_snapshots` from data.json.

Usage:
    SUPABASE_URL=https://xxx.supabase.co \
    SUPABASE_SERVICE_ROLE_KEY=... \
    python supabase_sync.py                 # pulls today's ET date from data.json
    python supabase_sync.py --date 2026-11-01  # override snapshot_date
    python supabase_sync.py --source backfill  # mark as backfill
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from export_excel import PROPERTY_GROUPS, ALL_PROPS

FL_TZ = ZoneInfo("America/New_York")
TABLE = "property_snapshots"


REGION_BY_ABBR = {abbr: region for region, abbrs in PROPERTY_GROUPS for abbr in abbrs}


def _n(v):
    """Coerce to float; empty/None → 0.0."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(v):
    """Coerce to int; empty/None → 0."""
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _rentable(p):
    return (_i(p.get("vacantReady")) + _i(p.get("vacantNotReady"))
            + _i(p.get("ntv")) - _i(p.get("vacantLeased"))
            - _i(p.get("ntvLeased")))


def _occ_rented(p):
    total = _i(p.get("total"))
    if not total:
        return 0.0
    vac = _i(p.get("vacantReady")) + _i(p.get("vacantNotReady"))
    leased = _i(p.get("vacantLeased"))
    return round((total - vac + leased) / total * 100, 2)


def _conv_pct(prospects, signed):
    if not prospects:
        return 0.0
    return round(signed / prospects * 100, 1)


def _delinq_pct(net, rec):
    if not net:
        return 0.0
    return round(rec / net * 100, 2)


def _renewal_pct(bucket):
    renewed = _i(bucket.get("Renewed"))
    denom = (renewed + _i(bucket.get("Current"))
             + _i(bucket.get("NTV/Formers"))
             + _i(bucket.get("Under Eviction")))
    if not denom:
        return 0.0
    return round(renewed / denom * 100, 1)


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _bucket_label(snapshot_date, bucket_num):
    """Return 'Mon YYYY' for the expiring bucket (M1 = current month).

    Example: snapshot 2026-10-28, M3 -> 'Dec 2026', M4 -> 'Jan 2027'."""
    month_zero = snapshot_date.month - 1 + (bucket_num - 1)
    year = snapshot_date.year + month_zero // 12
    month = (month_zero % 12) + 1
    return f"{_MONTH_ABBR[month - 1]} {year}"


def property_snapshot_from_data_json(data, abbr, snapshot_date, source="live"):
    """Build the row dict for one property, keyed to SQL column names."""
    table = (data.get("tableData") or {}).get(abbr) or {}
    traffic = (data.get("trafficData") or {}).get(abbr) or {}
    leases = (data.get("leasesData") or {}).get(abbr) or {}
    expiring = leases.get("expiring") or {}
    renovation = leases.get("renovation") or {}
    work_orders = leases.get("work_orders") or {}
    future_moves = leases.get("future_moves") or {}
    income = ((data.get("financialData") or {}).get("income") or {}).get(abbr) or {}
    delinq = ((data.get("financialData") or {}).get("delinquency") or {}).get(abbr) or {}

    charge = _n(income.get("charge"))
    credit = _n(income.get("credit"))
    receivable = _n(income.get("receivable_mtd"))
    net = charge - credit

    prospects = _i(traffic.get("total"))
    signed_mtd = _i(leases.get("signed_leases_mtd"))

    row = {
        "snapshot_date": snapshot_date.isoformat() if isinstance(snapshot_date, date) else str(snapshot_date),
        "property_abbr": abbr,
        "region": REGION_BY_ABBR.get(abbr),
        "generated_at": data.get("generatedAt"),
        "source": source,

        # Unit Status
        "total_units": _i(table.get("total")),
        "occupied": _i(table.get("occupied")),
        "vacant_ready": _i(table.get("vacantReady")),
        "vacant_not_ready": _i(table.get("vacantNotReady")),
        "ntv": _i(table.get("ntv")),
        "under_eviction": _i(table.get("eviction")),
        "vacant_leased": _i(table.get("vacantLeased")),
        "ntv_leased": _i(table.get("ntvLeased")),
        "off_market": _i(table.get("offMarket")),
        "rentable_units": _rentable(table),
        "physical_occupancy_pct": _n(table.get("occupancy")),
        "occupancy_rented_pct": _occ_rented(table),

        # Traffic MTD
        "prospects_mtd": prospects,
        "daily_avg_prospects": _n(traffic.get("daily_avg")),
        "signed_leases_mtd": signed_mtd,
        "conversion_rate_pct": _conv_pct(prospects, signed_mtd),
        "applicants_mtd": _i(leases.get("applicants_mtd")),
        "applicants_mtd_approved": _i(leases.get("applicants_mtd_approved")),
        "applicants_mtd_denied": _i(leases.get("applicants_mtd_denied")),
        "applicants_mtd_cancelled": _i(leases.get("applicants_mtd_cancelled")),
        "applicants_mtd_active": _i(leases.get("applicants_mtd_active")),

        # Traffic Snapshot
        "total_applicants": _i(leases.get("total_applicants")),
        "pending_approval": _i(leases.get("pending_approval")),
        "approved": _i(leases.get("approved")),
        "signed_lease": _i(leases.get("signed_lease")),

        # Renovation
        "renovated": _i(renovation.get("renovated")),
        "partial_renovated": _i(renovation.get("partial")),
        "classic": _i(renovation.get("classic")),

        # Work Orders
        "work_orders_open_total": _i(work_orders.get("open_total")),
        "work_orders_new_24h": _i(work_orders.get("new_24h")),

        # Future moves
        "future_mi_count": _i(future_moves.get("future_mi")),
        "future_mo_count": _i(future_moves.get("future_mo")),

        # Financial
        "income_total": charge,
        "credit_total": credit,
        "income_net": net,
        "receivable_mtd": receivable,
        "delinquency_pct": _delinq_pct(net, receivable),
        "delinq_current_amount": _n((delinq.get("current") or {}).get("amount")),
        "delinq_current_count": _i((delinq.get("current") or {}).get("count")),
        "delinq_eviction_amount": _n((delinq.get("eviction") or {}).get("amount")),
        "delinq_eviction_count": _i((delinq.get("eviction") or {}).get("count")),
        "delinq_formers_amount": _n((delinq.get("formers") or {}).get("amount")),
        "delinq_formers_count": _i((delinq.get("formers") or {}).get("count")),
    }

    # Expiring Leases m1..m4
    snap_d = snapshot_date if isinstance(snapshot_date, date) else \
        datetime.strptime(str(snapshot_date), "%Y-%m-%d").date()
    for i, key in enumerate(("month1", "month2", "month3", "month4"), start=1):
        bucket = expiring.get(key) or {}
        row[f"exp_m{i}_label"] = _bucket_label(snap_d, i)
        row[f"exp_m{i}_current"] = _i(bucket.get("Current"))
        row[f"exp_m{i}_pending_renewal"] = _i(bucket.get("Pending Renewal"))
        row[f"exp_m{i}_renewed"] = _i(bucket.get("Renewed"))
        row[f"exp_m{i}_ntv_formers"] = _i(bucket.get("NTV/Formers"))
        row[f"exp_m{i}_under_eviction"] = _i(bucket.get("Under Eviction"))
        row[f"exp_m{i}_renewal_pct"] = _renewal_pct(bucket)

    # Raw forensic blob — the per-property slices only (not the whole dump).
    row["raw_jsonb"] = {
        "tableData": table,
        "trafficData": traffic,
        "leasesData": leases,
        "income": income,
        "delinquency": delinq,
    }

    return row


def _normalize_batch(rows):
    """PostgREST requires every object in a batch to have identical keys AND
    distinct conflict-key values. Dedupe on (property_abbr, snapshot_date)
    keeping the last occurrence, sort by (snapshot_date, property_abbr) so
    insertion ids read naturally, then pad missing keys with None."""
    deduped = {}
    for row in rows:
        key = (row.get("property_abbr"), row.get("snapshot_date"))
        deduped[key] = row
    rows = sorted(
        deduped.values(),
        key=lambda r: (r.get("snapshot_date") or "", r.get("property_abbr") or ""),
    )
    all_keys = set()
    for row in rows:
        all_keys.update(row.keys())
    return [{k: row.get(k) for k in all_keys} for row in rows]


def upsert_snapshots(rows, supabase_url, service_role_key, dry_run=False):
    """Upsert all rows via Supabase REST. One request for the whole batch."""
    if dry_run:
        print(f"[dry-run] would upsert {len(rows)} row(s). Sample:")
        sample = {k: v for k, v in rows[0].items() if k != "raw_jsonb"}
        print(json.dumps(sample, indent=2, default=str))
        return

    base = supabase_url.rstrip("/")
    if base.endswith("/rest/v1"):
        base = base[: -len("/rest/v1")]
    url = f"{base}/rest/v1/{TABLE}?on_conflict=property_abbr,snapshot_date"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    payload = _normalize_batch(rows)
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(
            f"Supabase upsert failed: {resp.status_code} {resp.text}"
        )
    print(f"OK -- upserted {len(rows)} row(s) into {TABLE}.")


def _snapshot_date_from_data_json(data):
    """ET calendar date for the generatedAt timestamp."""
    ts = data.get("generatedAt")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone(FL_TZ).date()
        except (TypeError, ValueError):
            pass
    return datetime.now(FL_TZ).date()


def date_already_recorded(supabase_url, service_role_key, snapshot_date):
    """Guard: return True if any row exists for this snapshot_date."""
    base = supabase_url.rstrip("/")
    if base.endswith("/rest/v1"):
        base = base[: -len("/rest/v1")]
    resp = requests.get(
        f"{base}/rest/v1/{TABLE}"
        f"?snapshot_date=eq.{snapshot_date}&select=id&limit=1",
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return bool(resp.json())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data.json",
                        help="Path to data.json (default: ./data.json)")
    parser.add_argument("--date",
                        help="Override snapshot_date (YYYY-MM-DD). "
                             "Defaults to ET date of data.json generatedAt.")
    parser.add_argument("--source", default="live", choices=("live", "backfill"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the row that would be upserted; skip HTTP.")
    parser.add_argument("--guard-today", action="store_true",
                        help="Skip if any row already exists for this snapshot_date.")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: {data_path} not found. Run generate_dashboard.py first.",
              file=sys.stderr)
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if args.date:
        snapshot_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        snapshot_date = _snapshot_date_from_data_json(data)

    rows = [
        property_snapshot_from_data_json(data, abbr, snapshot_date, args.source)
        for abbr in ALL_PROPS
    ]

    print(f"Snapshot date: {snapshot_date}  ({args.source})")
    print(f"Properties:    {', '.join(ALL_PROPS)}")

    if args.dry_run:
        upsert_snapshots(rows, "", "", dry_run=True)
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.",
              file=sys.stderr)
        sys.exit(1)

    if args.guard_today and date_already_recorded(url, key, snapshot_date):
        print(f"Already recorded for {snapshot_date} -- skipping (guard-today).")
        return

    upsert_snapshots(rows, url, key)


if __name__ == "__main__":
    main()
