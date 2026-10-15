"""
Batch orchestrator for the dashboard's Send All flow.

Reads a JSON file dumped by the Vercel dispatcher (client_payload fed into
the send-renewal-letters.yml workflow):

    {
      "runId":  "<uuid>",
      "dryRun": true | false,
      "letters": [
        {"property": "49THSTR", "month": "2026-12", "unit": "1015",
         "resident": "Jane Doe"},
        ...
      ]
    }

Groups the letters by (property, month), then for each group:
  1. Generates PDFs via generate_letters.generate(..., units=..., out_root=...)
  2. Sends via send_letters.run_one_property(cfg, ..., only_units=..., status_path=...)

The status file is initialised once up front (total = len(letters)) and all
per-unit results stream into it. The dashboard polls that file.

Usage:
  python send_batch.py <batch.json> --status-file <path> --headless [--dry-run]
"""
import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate_letters
import send_letters


def group_letters(letters: list[dict]) -> "OrderedDict[tuple[str, str], list[str]]":
    """Group by (property_abbr_upper, YYYY-MM) -> ordered list of units."""
    groups: "OrderedDict[tuple[str, str], list[str]]" = OrderedDict()
    for L in letters:
        prop = str(L.get("property", "")).strip().upper()
        month = str(L.get("month", "")).strip()
        unit = str(L.get("unit", "")).strip()
        if not prop or not month or not unit:
            continue
        groups.setdefault((prop, month), []).append(unit)
    return groups


def main() -> int:
    ap = argparse.ArgumentParser(description="Send All orchestrator (generate + send).")
    ap.add_argument("batch_json", help="Path to the batch JSON file.")
    ap.add_argument("--status-file", required=True,
                    help="Streaming status JSON (dashboard polls this).")
    ap.add_argument("--headless", action="store_true", help="Playwright headless.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't click Send; stage every unit (STAGED status).")
    ap.add_argument("--pdf-root", default="",
                    help="Where PDFs are generated (default: ./generated-letters).")
    args = ap.parse_args()

    batch = json.loads(Path(args.batch_json).read_text(encoding="utf-8"))
    run_id = batch.get("runId", "")
    letters = batch.get("letters", [])
    dry_run = bool(args.dry_run or batch.get("dryRun"))
    if not run_id or not letters:
        print("ERROR: batch missing runId or letters", flush=True)
        return 2

    status_path = Path(args.status_file).expanduser().resolve()
    status_path.parent.mkdir(parents=True, exist_ok=True)
    send_letters.status_init(status_path, run_id, len(letters), dry_run)

    pdf_root = Path(args.pdf_root).expanduser().resolve() if args.pdf_root else (HERE / "generated-letters")

    groups = group_letters(letters)
    print(f"=== send_batch: runId={run_id} dryRun={dry_run} ===", flush=True)
    print(f"Groups: {len(groups)}  Total letters: {len(letters)}", flush=True)

    overall_rc = 0
    for (prop, month), units in groups.items():
        print(f"\n--- {prop} / {month} / {len(units)} units ---", flush=True)
        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            print(f"  bad month {month!r}, skipping group", flush=True)
            overall_rc = 2
            continue

        # 1. Generate PDFs for this group's units.
        gen_rc = generate_letters.generate(
            prop, dt.year, dt.month,
            units=units,
            out_root=pdf_root,
        )
        if gen_rc not in (0, 1):
            print(f"  generate_letters returned rc={gen_rc}; skipping send", flush=True)
            overall_rc = gen_rc
            continue

        # 2. Resolve send config (pointing at the same pdf_root).
        try:
            cfg = send_letters.resolve_config(prop, month, pdf_root=pdf_root)
        except ValueError as e:
            print(f"  resolve_config failed: {e}", flush=True)
            overall_rc = 2
            continue

        # 3. Run the send flow for this group.
        rc = send_letters.run_one_property(
            cfg,
            headless=args.headless,
            dry_run=dry_run,
            restart=True,            # dashboard flows don't use the resumable progress file
            only_units=units,
            status_path=status_path,
        )
        if rc != 0:
            overall_rc = rc

    send_letters.status_finalize(status_path)
    print(f"\nBatch complete. rc={overall_rc}  status={status_path}", flush=True)
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
