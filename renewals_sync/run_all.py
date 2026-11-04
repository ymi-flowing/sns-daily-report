"""
Renewal Letters - Batch runner for the sheets-direct import path.

Usage:
  python run_all.py <YYYY-MM>                          # all props except MP
  python run_all.py <YYYY-MM> ORT AP TV MOC ...        # explicit subset

Runs `import_to_sheets.py <ABBR> <YYYY-MM>` for each property. Each import:
  - reads R&F FRESH from the sheet's 'Rent & Fees' tab (no local cache)
  - appends expiring-lease rows to the property's tab
  - propagates formulas
  - auto-invokes sync_renewal_status.run_sync() to fill F, S, and Lease ID

MP is intentionally skipped (user's decision — see memory `renewal-letters-sheets-migration`).
TRA and AN3 both feed the shared TRA-AN3 tab; they run separately.
"""
import re
import subprocess
import sys
from datetime import datetime

# Default property list. Order chosen roughly small-to-large so a failure
# surfaces quickly. MP is intentionally omitted.
PROPERTIES = [
    "49THSTR", "ORT", "AP", "TV", "MOC", "MUSO",
    "SJ", "ROP", "EAST", "TRA", "AN3",
]

SYNC_KEYS = ("renewed", "lower_offer", "pending_renewal", "mtm", "current",
             "moved_out", "ntv", "eviction", "unmatched", "other")


def parse_results(output: str) -> tuple[int, int, dict]:
    """Extract matched, appended, and sync stats from import_to_sheets.py output."""
    matched = re.search(r"(\d+) match expiring=", output)
    matched_count = int(matched.group(1)) if matched else 0

    appended = re.search(r"appending (\d+) rows", output)
    appended_count = int(appended.group(1)) if appended else 0

    stats: dict[str, int] = {}
    # Sync prints lines like:  "    renewed      12"
    for key in SYNC_KEYS:
        m = re.search(rf"^\s+{re.escape(key)}\s+(\d+)\s*$", output, re.MULTILINE)
        if m:
            stats[key] = int(m.group(1))
    return matched_count, appended_count, stats


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run_all.py <YYYY-MM> [ABBR ...]")
        print("Example: python run_all.py 2026-12")
        print("Example: python run_all.py 2026-12 ORT AP TV MOC MUSO SJ ROP EAST TRA AN3")
        return 2

    month = sys.argv[1]
    if not re.match(r"^\d{4}-\d{2}$", month):
        print(f"Error: bad month '{month}' — expected YYYY-MM")
        return 2

    props = [p.upper() for p in sys.argv[2:]] if len(sys.argv) > 2 else PROPERTIES

    start = datetime.now()
    bar = "=" * 72
    print(bar)
    print(f"        RENEWAL LETTERS - BATCH IMPORT ({month})")
    print(bar)
    print(f"Started:  {start:%Y-%m-%d %H:%M:%S}")
    print(f"Props:    {len(props)} — {', '.join(props)}")
    print(bar)

    results = []
    for i, prop in enumerate(props, 1):
        print(f"\n[{i}/{len(props)}] {prop}")
        print("-" * 72)
        try:
            r = subprocess.run(
                ["python", "import_to_sheets.py", prop, month],
                capture_output=True, text=True, timeout=900,
            )
            print(r.stdout)
            if r.stderr.strip():
                print("STDERR:\n" + r.stderr)
            matched, appended, stats = parse_results(r.stdout)
            results.append((prop, matched, appended, stats, r.returncode))
        except subprocess.TimeoutExpired:
            print(f"    TIMEOUT: {prop} exceeded 900s")
            results.append((prop, 0, 0, {}, "TIMEOUT"))
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append((prop, 0, 0, {}, f"ERROR: {e}"))

    end = datetime.now()
    print("\n" + bar)
    print("                              SUMMARY")
    print(bar)
    hdr = f"{'Prop':<8} {'Match':>5} {'Appd':>5} {'Ren':>4} {'Low':>4} {'Cur':>4} {'NTV':>4} {'MO':>4} {'Ev':>4} {'?':>4}"
    print(hdr)
    print("-" * 72)
    tot = {k: 0 for k in SYNC_KEYS} | {"matched": 0, "appended": 0}
    for prop, matched, appended, stats, rc in results:
        s = {k: stats.get(k, 0) for k in SYNC_KEYS}
        flag = "" if rc == 0 else f"  [{rc}]"
        print(f"{prop:<8} {matched:>5} {appended:>5} {s['renewed']:>4} "
              f"{s['lower_offer']:>4} {s['current']:>4} {s['ntv']:>4} "
              f"{s['moved_out']:>4} {s['eviction']:>4} {s['unmatched']:>4}{flag}")
        tot["matched"] += matched
        tot["appended"] += appended
        for k in SYNC_KEYS:
            tot[k] += s[k]
    print("-" * 72)
    print(f"{'TOTAL':<8} {tot['matched']:>5} {tot['appended']:>5} "
          f"{tot['renewed']:>4} {tot['lower_offer']:>4} {tot['current']:>4} "
          f"{tot['ntv']:>4} {tot['moved_out']:>4} {tot['eviction']:>4} "
          f"{tot['unmatched']:>4}")
    print(bar)
    print(f"Finished: {end:%Y-%m-%d %H:%M:%S}  (duration: {end - start})")
    print(bar)
    return 0 if all(rc == 0 for _, _, _, _, rc in results) else 1


if __name__ == "__main__":
    sys.exit(main())
