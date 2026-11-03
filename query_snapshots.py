"""Read-only inspection queries for property_snapshots.

Prints row counts + portfolio trend + per-property deltas so a human can
verify the daily snapshot pipeline is landing correct data.

Usage:
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python query_snapshots.py
    python query_snapshots.py --prop AP
"""
import argparse
import os
import sys

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prop", default="AP",
                        help="property_abbr to show per-day deltas for")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.",
              file=sys.stderr)
        sys.exit(1)

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    r = requests.get(
        f"{url}/rest/v1/property_snapshots"
        "?select=source,snapshot_date&order=snapshot_date.asc",
        headers=headers, timeout=30,
    )
    rows = r.json()
    dates = sorted({row["snapshot_date"] for row in rows})
    print(f"Total rows: {len(rows)}")
    if dates:
        print(f"Date range: {dates[0]} .. {dates[-1]} ({len(dates)} unique dates)")
    counts = {}
    for row in rows:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    print(f"By source:  {counts}")

    r2 = requests.get(
        f"{url}/rest/v1/portfolio_snapshots"
        "?select=snapshot_date,total_units,occupied,physical_occupancy_pct,"
        "prospects_mtd,signed_leases_mtd,receivable_mtd"
        "&order=snapshot_date.asc",
        headers=headers, timeout=30,
    )
    print("\nPortfolio over time:")
    for row in r2.json():
        print(f"  {row['snapshot_date']}: "
              f"occ={row['occupied']}/{row['total_units']} "
              f"({row['physical_occupancy_pct']}%)  "
              f"prospects_mtd={row['prospects_mtd']}  "
              f"signed_mtd={row['signed_leases_mtd']}  "
              f"rec=${row['receivable_mtd']}")

    r3 = requests.get(
        f"{url}/rest/v1/property_daily_deltas"
        "?select=snapshot_date,prospects_mtd,delta_prospects,"
        "signed_leases_mtd,delta_signed_leases"
        f"&property_abbr=eq.{args.prop}&order=snapshot_date.asc",
        headers=headers, timeout=30,
    )
    print(f"\n{args.prop} daily deltas:")
    for row in r3.json():
        print(f"  {row['snapshot_date']}: "
              f"prospects_mtd={row['prospects_mtd']} (d={row['delta_prospects']})  "
              f"signed_mtd={row['signed_leases_mtd']} (d={row['delta_signed_leases']})")


if __name__ == "__main__":
    main()
