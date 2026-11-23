"""
Renewal Letters - Bulk Renewal Status sync wrapper.

Runs `sync_renewal_status.run_sync()` for every property, in one process
(no subprocess overhead). Refreshes Renewal Status (col G), Actual Renewal
(col T), and Lease ID (col AK) from ResMan into every property tab.

Usage:
  python sync_all.py                         # all props, write live
  python sync_all.py --dry-run               # preview, no writes
  python sync_all.py ORT AP TV               # explicit subset

Property order mirrors run_all.py (small-to-large; MP skipped). TRA covers
both TRA and AN3 — they share the TRA-AN3 tab, and the sync fetches both
ResMan properties and merges.
"""
import argparse
import sys
from datetime import datetime

from sync_renewal_status import run_sync

PROPERTIES = [
    "49THSTR", "ORT", "AP", "TV", "MOC", "MUSO",
    "SJ", "ROP", "EAST", "TRA",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("props", nargs="*", help="Property abbreviations (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    props = [p.upper() for p in args.props] if args.props else PROPERTIES

    start = datetime.now()
    bar = "=" * 72
    print(bar)
    print(f"        RENEWAL STATUS - BULK SYNC{' (dry-run)' if args.dry_run else ''}")
    print(bar)
    print(f"Started:  {start:%Y-%m-%d %H:%M:%S}")
    print(f"Props:    {len(props)} - {', '.join(props)}")
    print(bar)

    results = {}
    for i, prop in enumerate(props, 1):
        print(f"\n[{i}/{len(props)}] {prop}")
        print("-" * 72)
        try:
            rc = run_sync(prop, dry_run=args.dry_run)
            results[prop] = rc
        except Exception as e:
            print(f"    ERROR on {prop}: {e}")
            results[prop] = -1

    end = datetime.now()
    print("\n" + bar)
    print("                              SUMMARY")
    print(bar)
    for prop, rc in results.items():
        if rc == 0:
            flag = "OK"
        elif rc == 3:
            flag = "SKIPPED (ResMan returned 0 leases — existing data preserved)"
        else:
            flag = f"FAIL (rc={rc})"
        print(f"  {prop:<10} {flag}")
    print(bar)
    print(f"Finished: {end:%Y-%m-%d %H:%M:%S}  (duration: {end - start})")
    print(bar)

    # rc=3 (ResMan empty-response skip) is a warning, not a failure:
    # the sheet still has its last-good data and the bake step should run.
    return 0 if all(rc in (0, 3) for rc in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
