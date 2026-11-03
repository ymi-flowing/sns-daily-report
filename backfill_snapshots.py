"""
Parse historical SNS Daily Report xlsx files and upsert into property_snapshots.

Expects filenames shaped like `dashboard-YYYY-MM-DD.xlsx` or any .xlsx whose
first row contains a date (e.g. "SNS Daily Report  —  October 28, 2026 ...").

Usage:
    python backfill_snapshots.py --dir backfill/ --dry-run
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
    python backfill_snapshots.py --dir backfill/ --apply
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from export_excel import ALL_PROPS, PROPERTY_GROUPS
from supabase_sync import TABLE, _bucket_label, upsert_snapshots

REGION_BY_ABBR = {abbr: region for region, abbrs in PROPERTY_GROUPS for abbr in abbrs}


# --- Label -> column mapping ---------------------------------------------

# Keys are the *normalised* excel label (whitespace collapsed, lowered).
# Values are the SQL column name.
_UNIT_STATUS_MAP = {
    "total units": "total_units",
    "occupied": "occupied",
    "vacant ready": "vacant_ready",
    "vacant not ready": "vacant_not_ready",
    "ntv": "ntv",
    "under eviction": "under_eviction",
    "physical occupancy %": "physical_occupancy_pct",
    "vacant leased": "vacant_leased",
    "ntv leased": "ntv_leased",
    "rentable units": "rentable_units",
    "off the market": "off_market",
    "occupancy rented %": "occupancy_rented_pct",
}

_TRAFFIC_MTD_MAP = {
    "total prospects (mtd)": "prospects_mtd",
    "daily avg prospects": "daily_avg_prospects",
    "new signed leases (mtd)": "signed_leases_mtd",
    "conversion rate %": "conversion_rate_pct",
    "applicants mtd": "applicants_mtd",
    "approved (mtd)": "applicants_mtd_approved",
    "denied (mtd)": "applicants_mtd_denied",
    "cancelled (mtd)": "applicants_mtd_cancelled",
    "still active (mtd)": "applicants_mtd_active",
}

_TRAFFIC_SNAPSHOT_MAP = {
    "total applicants": "total_applicants",
    "pending approval": "pending_approval",
    "approved": "approved",
    "signed lease": "signed_lease",
}

_EXPIRING_ROW_MAP = {
    "current": "current",
    "pending renewal": "pending_renewal",
    "renewed": "renewed",
    "ntv/formers": "ntv_formers",
    "under eviction": "under_eviction",
    "renewal %": "renewal_pct",
}

_RENOVATION_MAP = {
    "renovated": "renovated",
    "partial renovated": "partial_renovated",
    "classic": "classic",
}

_WORK_ORDERS_MAP = {
    "open (non-make-ready)": "work_orders_open_total",
    "new in last 24h": "work_orders_new_24h",
}

_FUTURE_MOVES_MAP = {
    "future move-outs": "future_mo_count",
}

_FINANCIAL_MAP = {
    "total income": "income_total",
    "total credit": "credit_total",
    "net": "income_net",
    "receivable mtd": "receivable_mtd",
    "del %": "delinquency_pct",
    "current ($)": "delinq_current_amount",
    "current (#)": "delinq_current_count",
    "under eviction ($)": "delinq_eviction_amount",
    "under eviction (#)": "delinq_eviction_count",
    "formers ($)": "delinq_formers_amount",
    "formers (#)": "delinq_formers_count",
}

# Section headers that switch parser mode.
_SECTIONS = {
    "unit status": "unit_status",
    "traffic": "traffic_combined",            # pre-2026-10-28 single section
    "traffic month-to-date": "traffic_mtd",   # 2026-10-28 onward
    "traffic snapshot": "traffic_snapshot",   # 2026-10-28 onward
    "expiring leases (current + next 3 months)": "expiring",
    "renovation status": "renovation",
    "work orders": "work_orders",
    "future moves (current month)": "future_moves",
    "financial summary": "financial",
}

_EXPIRING_SKIP = {"4-month total", "total expiring"}


def _norm(s):
    """Lowercase + collapse whitespace."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _as_number(v):
    """Numeric cells → int when whole, float otherwise. Postgres INT columns
    reject '1.0' via PostgREST, so we strip the fractional part when it's zero."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _parse_date_from_filename(path):
    # ISO: dashboard-2026-10-28.xlsx
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", path.stem)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    # US: "SNS Daily 10_28_2026, 11_00 AM.xlsx" -> M_D_YYYY
    m = re.search(r"(?<!\d)(\d{1,2})_(\d{1,2})_(\d{4})(?!\d)", path.stem)
    if m:
        try:
            return date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    return None


def _parse_date_from_title_cell(cell_value):
    """Extracts date from strings like 'SNS Daily Report  —  October 28, 2026 ...'."""
    if not cell_value:
        return None
    m = re.search(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        str(cell_value),
    )
    if not m:
        return None
    try:
        return datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%B %d %Y").date()
    except ValueError:
        return None


def _month_number_from_label(label):
    """Normalise a month subheader like 'Oct' / 'October' / 'Nov 2026' → 1..12."""
    if not label:
        return None
    txt = _norm(label).split()[0][:3]
    months = {m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul",
         "aug", "sep", "oct", "nov", "dec"], start=1)}
    return months.get(txt)


def _expiring_month_index(month_label, snapshot_date):
    """Return 1..4 for M1..M4 bucket, given the subheader month label and
    the snapshot date. M1 is the current month."""
    m = _month_number_from_label(month_label)
    if m is None:
        return None
    diff = (m - snapshot_date.month) % 12
    if 0 <= diff <= 3:
        return diff + 1
    return None


# --- Parse one workbook --------------------------------------------------


def parse_workbook(path, snapshot_date=None):
    """Return a list of row dicts (one per property) suitable for upsert."""
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.active  # "Dashboard" is the only sheet

    if snapshot_date is None:
        snapshot_date = _parse_date_from_filename(path)
    if snapshot_date is None:
        snapshot_date = _parse_date_from_title_cell(ws.cell(row=1, column=1).value)
    if snapshot_date is None:
        raise ValueError(f"Could not infer snapshot_date for {path.name}")

    # Property columns start at B. Row 3 holds the property headers.
    header_row = [ws.cell(row=3, column=c).value for c in range(2, 2 + len(ALL_PROPS))]
    abbrs = [str(v).strip() for v in header_row]
    # If the file was generated with the same code, abbrs will equal ALL_PROPS.
    # If not, we trust the header row.

    # Prepare per-property blank rows.
    rows = {
        abbr: {
            "snapshot_date": snapshot_date.isoformat(),
            "property_abbr": abbr,
            "region": REGION_BY_ABBR.get(abbr),
            "source": "backfill",
        }
        for abbr in abbrs
    }

    section = None
    current_expiring_m = None

    # Rows start at 4 (header rows are 1..3).
    for r in range(4, ws.max_row + 1):
        label_raw = ws.cell(row=r, column=1).value
        label = _norm(label_raw)
        if not label:
            continue

        # Section header?
        if label in _SECTIONS:
            section = _SECTIONS[label]
            current_expiring_m = None
            continue

        # Expiring subheader (month name)?
        if section == "expiring":
            if label in _EXPIRING_SKIP:
                # "Total Expiring" / "4-Month Total" — skip the row but
                # keep current_expiring_m so a following "Renewal %" still
                # attaches to the right bucket.
                if label == "4-month total":
                    current_expiring_m = None
                continue
            m_idx = _expiring_month_index(label_raw, snapshot_date)
            if m_idx is not None:
                current_expiring_m = m_idx
                label = _bucket_label(snapshot_date, m_idx)
                for abbr in abbrs:
                    rows[abbr][f"exp_m{m_idx}_label"] = label
                continue

        values = [_as_number(ws.cell(row=r, column=c).value)
                  for c in range(2, 2 + len(abbrs))]

        if section == "unit_status" and label in _UNIT_STATUS_MAP:
            col = _UNIT_STATUS_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "traffic_mtd" and label in _TRAFFIC_MTD_MAP:
            col = _TRAFFIC_MTD_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "traffic_snapshot" and label in _TRAFFIC_SNAPSHOT_MAP:
            col = _TRAFFIC_SNAPSHOT_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "traffic_combined":
            # Pre-2026-10-28 layout: MTD + snapshot rows mixed under one header.
            col = _TRAFFIC_MTD_MAP.get(label) or _TRAFFIC_SNAPSHOT_MAP.get(label)
            if col:
                for abbr, v in zip(abbrs, values):
                    rows[abbr][col] = v

        elif section == "expiring" and current_expiring_m and label in _EXPIRING_ROW_MAP:
            suffix = _EXPIRING_ROW_MAP[label]
            col = f"exp_m{current_expiring_m}_{suffix}"
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "renovation" and label in _RENOVATION_MAP:
            col = _RENOVATION_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "work_orders" and label in _WORK_ORDERS_MAP:
            col = _WORK_ORDERS_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "future_moves" and label in _FUTURE_MOVES_MAP:
            col = _FUTURE_MOVES_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

        elif section == "financial" and label in _FINANCIAL_MAP:
            col = _FINANCIAL_MAP[label]
            for abbr, v in zip(abbrs, values):
                rows[abbr][col] = v

    wb.close()
    return list(rows.values())


# --- CLI -----------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True,
                        help="Directory containing historical .xlsx files.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and print per-file summaries; skip HTTP.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually upsert to Supabase.")
    parser.add_argument("--file", help="Parse just one file (debug).")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Pick one: --dry-run or --apply", file=sys.stderr)
        sys.exit(2)

    directory = Path(args.dir)
    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(directory.glob("*.xlsx"))
        if not files:
            print(f"No .xlsx files found under {directory}", file=sys.stderr)
            sys.exit(1)

    all_rows = []
    for f in files:
        try:
            rows = parse_workbook(f)
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIP {f.name}: {exc}", file=sys.stderr)
            continue
        snapshot_date = rows[0]["snapshot_date"] if rows else "?"
        print(f"  {f.name}  ->  {snapshot_date}  ({len(rows)} properties)")
        all_rows.extend(rows)

    if not all_rows:
        print("Nothing to upsert.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"\n[dry-run] parsed {len(all_rows)} row(s) across {len(files)} file(s).")
        print("Sample row:")
        print(json.dumps(all_rows[0], indent=2, default=str))
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.",
              file=sys.stderr)
        sys.exit(1)

    upsert_snapshots(all_rows, url, key)


if __name__ == "__main__":
    main()
