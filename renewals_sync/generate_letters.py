"""
Batch-generate renewal-letter PDFs for a property + expiring month.

Vendored from Renewal Letters/letters/generate_letters.py; keep in lockstep.
Changes from upstream:
  - sheets_client / letter_config imported from this dir (not a sibling pkg)
  - --units filters to a subset of unit numbers (dashboard Send All flow)
  - --out-dir overrides the default output location (GH Actions runner)

Output layout (default): <out_dir>/<Property formal name>/<Month Year>/*.pdf

Usage:
  python generate_letters.py 49THSTR 2026-12
  python generate_letters.py 49THSTR 2026-12 --units 318,515,901
  python generate_letters.py 49THSTR 2026-12 --out-dir /tmp/pdfs
"""
import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import sheets_client
from letter_config import PROPERTY_NAMES

TAB_NAMES = {
    "49THSTR": "49thStr",
    "TRA":     "TRA-AN3",
    "AN3":     "TRA-AN3",
}

# Property abbrs where col AA (formerly Internet) is labelled Landscaping in the letter.
LANDSCAPING_PROPS = {"TRA", "AN3"}

# Short "Month YYYY" folder tag (matches Send Letters convention: "Dec 2026").
def month_folder_label(year: int, month: int) -> str:
    return datetime(year, month, 1).strftime("%b %Y")

# File-name prefix per property abbr (matches send_letters.py PDF_PATTERN).
# Keep in sync with FILE_PREFIX in Send Letters/send_letters.py.
FILE_PREFIX = {
    "49THSTR": "49th",
    "ORT":     "The Villas",
    "AP":      "Arbor Place",
    "TV":      "The Vista",
    "MOC":     "The Morgan",
    "MUSO":    "The Mustang",
    "SJ":      "San Jose",
    "ROP":     "Riverside",
    "EAST":    "Eastpointe",
    "TRA":     "The River",
    "AN3":     "AN3",
}


TEMPLATE_PATH = HERE / "letter_template.html"


def render_template(context: dict) -> str:
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    for k, v in context.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def parse_sheet_date(raw, expected_year: int, expected_month: int) -> datetime | None:
    """
    Sheet Exp Date cells can be:
      - MM/DD/YYYY text (e.g. "12/30/2026") — trust it.
      - ISO with time (e.g. "2026-12-01T03:00:00.000Z") — Sheets stored a Date.
        Sheets locale can flip DD/MM on input where day <= 12; if the parsed
        month != expected_month AND day == expected_month, swap them.

    Returns a datetime or None if unparseable.
    """
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    if "/" in s:
        try:
            return datetime.strptime(s[:10], "%m/%d/%Y")
        except ValueError:
            pass
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None
    if d.month != expected_month and d.day == expected_month:
        try:
            return d.replace(month=d.day, day=d.month)
        except ValueError:
            pass
    return d


def money(v) -> str:
    """Format a number as $1,234. Blank/0/'' -> ''."""
    if v is None or v == "" or v == 0 or v == "0":
        return ""
    try:
        n = float(str(v).replace(",", "").replace("$", ""))
        if n == 0:
            return ""
        return "${:,.0f}".format(n)
    except (ValueError, TypeError):
        return ""


def num(v):
    """Parse to float or None."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None


def build_charges_rows(row_dict: dict, abbr: str) -> str:
    """
    Build HTML table rows for charges. Skip any charge that's $0/blank.
    Rent (col P) is always shown even if 0.
    """
    z_label = "Landscaping" if abbr in LANDSCAPING_PROPS else "Internet"

    lines: list[tuple[str, str]] = []
    # (label, sheet col header)
    rent_disp = money(row_dict.get("Rent A")) or "$0"
    lines.append(("Rent", rent_disp))

    for label, header in [
        ("Utility",                  "Utility"),
        ("Valet Trash",              "Valet"),
        ("Pest Control",             "Pest"),
        ("Property's Amenities Fee", "Amenity"),
        (z_label,                    "Internet"),
        ("Pet Rent",                 "Pet"),
        ("Reserved Parking",         "Parking"),
        ("Washer & Dryer",           "WD"),
        ("Storage",                  "Storage"),
    ]:
        disp = money(row_dict.get(header))
        if disp:
            lines.append((label, disp))

    html = []
    for label, val in lines:
        html.append(f'              <tr><td class="label">{label}</td><td class="value">{val}</td></tr>')
    return "\n".join(html)


def build_deposit_block(row_dict: dict) -> str:
    """
    Return the deposit-inline HTML block iff 'Deposit D' > 0; else empty string.
    """
    dep_addition = num(row_dict.get("Deposit D"))
    if not dep_addition or dep_addition <= 0:
        return ""
    held = money(row_dict.get("Deposit")) or "$0"
    required = money(row_dict.get("R Deposit")) or "$0"
    addition = money(row_dict.get("Deposit D")) or "$0"
    return (
        '            <div class="deposit-inline">\n'
        f'              <span class="item">Security Deposit Held: <strong>{held}</strong></span>\n'
        f'              <span class="item">Required Deposit: <strong>{required}</strong></span>\n'
        f'              <span class="item">Required Addition: <strong>{addition}</strong></span>\n'
        '            </div>'
    )


def first_name_of(full: str) -> str:
    if not full:
        return ""
    return str(full).strip().split(" ")[0]


def row_context(row_dict: dict, abbr: str, property_name: str,
                header_date_str: str, year: int, month: int) -> dict | None:
    """Return the template context dict, or None if the row is unrenderable."""
    unit = str(row_dict.get("Unit", "")).strip()
    resident = str(row_dict.get("Resident Name", "")).strip()
    floorplan = str(row_dict.get("Floorplan", "")).strip()
    if not unit or not resident:
        return None

    exp_dt = parse_sheet_date(row_dict.get("Exp Date"), year, month)
    if not exp_dt:
        return None
    exp_str = exp_dt.strftime("%m/%d/%Y")
    lease_start = (exp_dt + timedelta(days=1)).strftime("%m/%d/%Y")

    total_display = money(row_dict.get("Total Offer A")) or "$0"

    return {
        "property_name": property_name,
        "header_date":   header_date_str,
        "first_name":    first_name_of(resident),
        "resident_name": resident,
        "apartment":     unit,
        "floorplan":     floorplan,
        "exp_date":      exp_str,
        "lease_start":   lease_start,
        "month_label":   datetime(year, month, 1).strftime("%b %Y"),
        "charges_rows":  build_charges_rows(row_dict, abbr),
        "deposit_block": build_deposit_block(row_dict),
        "total_display": total_display,
    }


# Renewal Status values that get a letter. Everything else (Under Eviction,
# Renewed, Lower Offer, Moved Out, Cancelled, Evicted, Denied, blanks) is skipped.
LETTER_STATUSES = {"Current", "NTV"}


def _get_range_with_retry(tab: str, range_a1: str, attempts: int = 4) -> dict:
    """Retry get_range on transient HTTP errors (Apps Script webhook 404s).
    Safe because get_range is a pure read.
    """
    import time
    last = None
    for i in range(attempts):
        try:
            return sheets_client.get_range(tab, range_a1)
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(2 ** i)
                continue
    raise last


def load_month_rows(abbr: str, year: int, month: int) -> tuple[list[dict], dict]:
    """Read the property tab and return (eligible_rows, skip_report).

    Eligible rows match the target Expiring Month AND have Renewal Status in
    LETTER_STATUSES. Skip report counts by status for anything filtered out.
    """
    tab = TAB_NAMES.get(abbr, abbr)
    data = _get_range_with_retry(tab, "A1:AJ500")
    values = data.get("values", [])
    if not values:
        return [], {}
    headers = values[0]
    wanted_iso = f"{year:04d}-{month:02d}"
    wanted_text = datetime(year, month, 1).strftime("%B %Y")
    out: list[dict] = []
    skipped: dict = {}
    for row in values[1:]:
        if not row or not str(row[0]).strip():
            continue
        d = {h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)}
        em = str(d.get("Expiring Month", "")).strip()
        if not (em.startswith(wanted_iso) or em == wanted_text):
            continue
        status = str(d.get("Renewal Status", "")).strip() or "(blank)"
        if status not in LETTER_STATUSES:
            skipped[status] = skipped.get(status, 0) + 1
            continue
        out.append(d)
    return out, skipped


def generate(abbr: str, year: int, month: int,
             units: list[str] | None = None,
             out_root: Path | None = None) -> int:
    property_name = PROPERTY_NAMES.get(abbr)
    if not property_name:
        print(f"ERROR: unknown property abbr '{abbr}' — extend PROPERTY_NAMES in letter_config.py")
        return 2

    prefix = FILE_PREFIX.get(abbr)
    if not prefix:
        print(f"ERROR: no filename prefix for '{abbr}' — extend FILE_PREFIX in generate_letters.py")
        return 2

    rows, skipped = load_month_rows(abbr, year, month)
    if units:
        wanted = {str(u).strip() for u in units}
        rows = [r for r in rows if str(r.get("Unit", "")).strip() in wanted]
        print(f"[{abbr}] --units filter: {len(rows)} / {len(wanted)} units matched")
    if not rows:
        print(f"No eligible rows for {abbr} {year}-{month:02d} — nothing to generate.")
        if skipped:
            print(f"  filtered out by status: {skipped}")
        return 1

    month_tag = month_folder_label(year, month)          # e.g. "Dec 2026"
    out_root = out_root or (PROJECT_ROOT / "Generated Letters")
    out_dir = out_root / property_name / month_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    header_date_str = datetime.now().strftime("%m/%d/%Y")
    print(f"[{abbr}] {property_name} — {month_tag} — {len(rows)} eligible")
    if skipped:
        print(f"  skipped by status: {skipped}")
    print(f"  output: {out_dir}")

    generated_pdfs: list[Path] = []
    skipped: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.emulate_media(media="print")

        for r in rows:
            ctx = row_context(r, abbr, property_name, header_date_str, year, month)
            if not ctx:
                skipped.append(f"unit {r.get('Unit', '?')}: missing data")
                continue

            html = render_template(ctx)
            page.set_content(html, wait_until="networkidle")

            unit = ctx["apartment"]
            filename = f"{prefix} - {month_tag} - {unit}.pdf"
            pdf_path = out_dir / filename
            page.pdf(path=str(pdf_path), print_background=True, prefer_css_page_size=True)
            generated_pdfs.append(pdf_path)
            print(f"    {filename}")

        browser.close()

    # Merge into one PDF.
    if generated_pdfs:
        merged_path = out_dir / f"{prefix} - {month_tag} - All Letters.pdf"
        writer = PdfWriter()

        def sort_key(p):
            """Numeric sort when unit is pure digits, else lexical.
            Filename shape: "<prefix> - <Mon YYYY> - <unit>.pdf"."""
            m = re.search(r"- ([A-Za-z0-9-]+)\.pdf$", p.name)
            u = m.group(1) if m else ""
            return (0, int(u)) if u.isdigit() else (1, u)

        for pdf in sorted(generated_pdfs, key=sort_key):
            writer.append(str(pdf))
        with open(merged_path, "wb") as f:
            writer.write(f)
        writer.close()
        print(f"  merged -> {merged_path.name} ({len(generated_pdfs)} pages)")

    if skipped:
        print(f"  SKIPPED ({len(skipped)}):")
        for s in skipped:
            print(f"    {s}")

    print(f"Done. {len(generated_pdfs)} letters generated.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate renewal letter PDFs.")
    ap.add_argument("abbr", help="Property abbreviation (e.g. 49THSTR)")
    ap.add_argument("month", help="Expiring month YYYY-MM")
    ap.add_argument("--units", default="",
                    help="Comma-separated unit numbers to filter (default: all eligible)")
    ap.add_argument("--out-dir", default="",
                    help="Root output directory (default: <project>/Generated Letters)")
    args = ap.parse_args()
    try:
        target = datetime.strptime(args.month, "%Y-%m")
    except ValueError:
        print(f"Bad month: {args.month} (expected YYYY-MM)")
        return 2
    units = [u.strip() for u in args.units.split(",") if u.strip()] or None
    out_root = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    return generate(args.abbr.upper(), target.year, target.month,
                    units=units, out_root=out_root)


if __name__ == "__main__":
    sys.exit(main())
