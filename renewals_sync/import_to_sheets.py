"""
Direct ResMan -> Google Sheets importer (no Monday.com writes).

Usage: python import_to_sheets.py 49THSTR 2026-12
Appends expiring-lease rows for the given month to the matching property tab
and propagates formula columns to the new rows.
"""
import math
import sys
from datetime import datetime

import requests
from requests.auth import HTTPBasicAuth

import sheets_client
from create_monday_items import (
    RESMAN_API_KEY, RESMAN_PARTNER_ID, RESMAN_ACCOUNT_ID, RESMAN_PARTNERS_URL,
    BOARD_IDS,
    TARGET_STATUSES,
    get_property_id, fetch_leases, filter_leases,
    parse_unit_type, get_resident_name, get_current_rent_info,
    fetch_deposit, normalize_sqft_key,
)


def _format_date_us(iso_text: str) -> str:
    """'2026-10-15' -> '10/15/2026'. Returns input unchanged if it doesn't parse."""
    if not iso_text:
        return ""
    try:
        return datetime.strptime(iso_text[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return iso_text

# Renovation index (0=PR, 1=Yes, 2=No) -> R&F rent tier label + sheet display
RENOVATION_LABEL = {0: "PR", 1: "Yes", 2: "No"}

# Sheet/board abbreviation -> ResMan abbreviation. Only entries that differ.
RESMAN_ABBR = {
    "49THSTR": "49thStr",
}

# Abbr -> actual sheet tab name (only entries that differ from the abbr).
TAB_NAMES = {
    "49THSTR": "49thStr",   # existing tab uses mixed case
    "TRA":     "TRA-AN3",
    "AN3":     "TRA-AN3",
}

# Charge categories to pull from ResMan Ledger. Extended vs Monday-side importer
# (added storage/garage).
CHARGE_MATCHERS = {
    "pet":     ["pet rent"],
    "parking": ["parking", "reserved"],
    "wd":      ["washer", "dryer", "w/d"],
    "storage": ["storage", "garage"],
}


def fetch_all_charges(property_id: int, billing_account_id: str) -> dict:
    """Extended fetch_recurring_charges — includes storage/garage."""
    headers = {"ResMan-Account-Id": RESMAN_ACCOUNT_ID, "Accept": "application/json"}
    first_of_month = datetime.now().replace(day=1).strftime("%Y-%m-%d 00:00:00")
    params = {"propertyId": property_id, "billingAccountId": billing_account_id, "postedSince": first_of_month}
    result = {k: 0 for k in CHARGE_MATCHERS}
    try:
        r = requests.get(f"{RESMAN_PARTNERS_URL}/Transactions/Ledger",
                         headers=headers, params=params,
                         auth=HTTPBasicAuth(RESMAN_PARTNER_ID, RESMAN_API_KEY), timeout=30)
        if not r.ok:
            return result
        for t in r.json().get("transactions", []):
            if t.get("type") != "Charge":
                continue
            cat = t.get("category", {})
            name = (cat.get("name", "") if isinstance(cat, dict) else str(cat)).lower()
            amount = t.get("amount", 0) or 0
            for key, patterns in CHARGE_MATCHERS.items():
                if any(p in name for p in patterns):
                    result[key] = max(result[key], amount)
                    break
    except Exception as e:
        print(f"  Ledger error for {billing_account_id}: {e}")
    return result


# Property abbreviation -> label used in the 'Rent & Fees' sheet tab's Property column.
# Only entries that differ from the abbr. (Other props match verbatim: ORT, SJ, etc.)
RF_SHEET_PROP = {
    "49THSTR": "49thStr",
    "AN3":     "TRA",  # AN3 shares R&F rows with TRA
}


def _rf_num(x):
    if x == "" or x is None:
        return ""
    try:
        v = float(x)
        return int(v) if v.is_integer() else v
    except (ValueError, TypeError):
        return x


def prefetch_rf_full(property_abbr: str) -> dict[str, dict]:
    """
    Read Rent & Fees for this property from the 'Rent & Fees' TAB of the target
    Google Sheet — the current source of truth (user maintains it live).

    Returns {sqft_str: {No, PR, Yes, utility, valet, pest, amenity, z_fee}}.
    z_fee is the value that lands in col Z of the tab. For TRA/AN3 the col Z
    header was renamed from "Internet" to "Landscaping" (they charge $39
    landscaping, no internet), so z_fee is sourced from R&F col L. For all
    other properties z_fee is sourced from R&F col K (Internet).

    IMPORTANT — do NOT re-add a Monday board fallback here. If a lookup misses,
    the R&F tab is out of date and the user needs to update it. Silent fallback
    to stale Monday data caused a Dec 2026 49thStr import to write wrong
    Utility/Amenity values on 2026-09-07.
    """
    sheet_prop = RF_SHEET_PROP.get(property_abbr, property_abbr)
    data = sheets_client.get_range("Rent & Fees", "A2:L100")
    result: dict[str, dict] = {}
    z_fee_col = 11 if sheet_prop == "TRA" else 10  # Landscaping vs Internet
    for row in data.get("values", []):
        r = list(row) + [""] * 12
        if str(r[0]).strip() != sheet_prop:
            continue
        sqft = str(r[1]).strip()
        if not sqft:
            continue
        result[sqft] = {
            "type":    str(r[2]).strip(),
            "No":      _rf_num(r[3]),
            "PR":      _rf_num(r[4]),
            "Yes":     _rf_num(r[5]),
            "utility": _rf_num(r[6]),
            "valet":   _rf_num(r[7]),
            "pest":    _rf_num(r[8]),
            "amenity": _rf_num(r[9]),
            "z_fee":   _rf_num(r[z_fee_col]),
        }
    return result


def derive_renewal_status(lease_status: str) -> str:
    s = (lease_status or "").lower()
    if "eviction" in s:
        return "Under Eviction"
    if "notice" in s or "ntv" in s:
        return "NTV"
    return ""


def months_between(mi_iso: str, exp_iso: str) -> int | str:
    if not mi_iso or not exp_iso:
        return ""
    try:
        mi = datetime.strptime(mi_iso[:10], "%Y-%m-%d")
        exp = datetime.strptime(exp_iso[:10], "%Y-%m-%d")
        return math.ceil((exp - mi).days / 30)
    except ValueError:
        return ""


# Sheet columns: 34 core + 1 Floorplan (col D) + 1 Lease ID (col AJ) = 36 total.
# If a tab has fewer cols, Sheets ignores trailing cells.
SHEET_COLS = 36
# Formula columns: (col_letter, header, template with {r})
# T ("Actual Renewal") intentionally omitted — filled by sync_renewal_status.py from
# ResMan history[]. U formula matches user's 2026-09-07 update (blank when T is empty).
FORMULAS = [
    ("P",  "Rent A",           "=M{r}+N{r}"),
    ("Q",  "Total Offer A",    "=P{r}+AF{r}"),
    ("R",  "Delta A",          "=Q{r}-L{r}"),
    ("S",  "Delta A %",        "=R{r}/L{r}"),
    ("U",  "Actual Delta",     '=IF(T{r}>0, T{r}-L{r}, "")'),
    ("V",  "Actual Delta %",   "=U{r}/L{r}"),
    ("AF", "Total Other",      "=SUM(W{r}:AE{r})"),
    ("AH", "R Deposit",        "=if(AG{r}>499, AG{r}, 499)"),
    ("AI", "Deposit D",        "=AH{r}-AG{r}"),
]


def build_row(lease, sqft_str, renov_idx, rf, charges, deposit_amount, expiring_label):
    renov_label = RENOVATION_LABEL.get(renov_idx, "")
    rnf_rent = rf.get(renov_label, "") if rf else ""
    base_rent, other = get_current_rent_info(lease.get("history", []))
    current_total = (base_rent or 0) + (other or 0)
    mi = lease.get("moveInDate", "") or ""
    exp = lease.get("leaseEndDate", "") or ""
    row = [""] * SHEET_COLS
    row[0]  = expiring_label                                    # A Expiring Month
    row[1]  = lease.get("unitNumber", "")                       # B Unit
    row[2]  = sqft_str                                          # C Unit Type
    row[3]  = rf.get("type", "") if rf else ""                  # D Floorplan (from R&F "Type")
    row[4]  = renov_label                                       # E Renovated
    row[5]  = get_resident_name(lease.get("people", []))        # F Resident Name
    row[6]  = derive_renewal_status(lease.get("leaseStatus", ""))  # G Renewal Status
    row[7]  = _format_date_us(mi)                               # H MI Date
    row[8]  = _format_date_us(exp)                              # I Exp Date
    row[9]  = months_between(mi, exp)                           # J Months in Property
    row[10] = base_rent or ""                                   # K Base Rent
    row[11] = current_total or ""                               # L Current Total
    row[12] = rnf_rent                                          # M RnF Rent
    row[13] = ""                                                # N Adjustment (manual)
    row[14] = 12                                                # O Terms A (default)
    # P..V formulas (skip)
    row[22] = rf.get("utility", "") if rf else ""               # W Utility
    row[23] = rf.get("valet", "")   if rf else ""               # X Valet
    row[24] = rf.get("pest", "")    if rf else ""               # Y Pest
    row[25] = rf.get("amenity", "") if rf else ""               # Z Amenity
    row[26] = rf.get("z_fee", "")   if rf else ""               # AA Internet (or Landscaping for TRA/AN3)
    row[27] = charges.get("pet", "") or ""                      # AB Pet
    row[28] = charges.get("parking", "") or ""                  # AC Parking
    row[29] = charges.get("wd", "") or ""                       # AD WD
    row[30] = charges.get("storage", "") or ""                  # AE Storage
    # AF formula (skip)
    row[32] = deposit_amount or ""                              # AG Deposit
    # AH, AI formulas (skip)
    row[35] = lease.get("leaseId", "")                          # AJ Lease ID (parent record UUID)
    return row


def propagate_formulas(tab: str, start_row: int, end_row: int) -> bool:
    """Returns True on success, False if set_range isn't deployed (soft-fail)."""
    try:
        for letter, _hdr, template in FORMULAS:
            formulas = [[template.format(r=r)] for r in range(start_row, end_row + 1)]
            rng = f"{letter}{start_row}:{letter}{end_row}"
            sheets_client.set_range(tab, rng, formulas, mode="formulas")
        return True
    except Exception as e:
        if "setRange is not defined" in str(e):
            return False
        raise


def tab_name_for(abbr: str) -> str:
    return TAB_NAMES.get(abbr, abbr)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python import_to_sheets.py <ABBR> <YYYY-MM>")
        return 2
    abbr, month = sys.argv[1].upper(), sys.argv[2]
    if abbr not in BOARD_IDS:
        print(f"Unknown property: {abbr}")
        return 2
    try:
        target = datetime.strptime(month, "%Y-%m")
    except ValueError:
        print(f"Bad month: {month}")
        return 2
    expiring_label = target.strftime("%B %Y")
    tab = tab_name_for(abbr)

    print(f"[{abbr}] {expiring_label} -> tab '{tab}'")

    property_id = get_property_id(RESMAN_ABBR.get(abbr, abbr))
    if not property_id:
        print(f"[{abbr}] could not resolve property_id")
        return 1
    print(f"  property_id={property_id}")

    leases = fetch_leases(property_id)
    print(f"  {len(leases)} leases fetched, filtering...")
    matched = filter_leases(leases, month)
    print(f"  {len(matched)} match expiring={month} + status in {TARGET_STATUSES}")

    rf_by_sqft = prefetch_rf_full(abbr)
    print(f"  R&F prefetched for {len(rf_by_sqft)} unit types: {sorted(rf_by_sqft.keys())}")

    rows = []
    for lease in matched:
        sqft, sqft_str, renov_idx = parse_unit_type(lease.get("unitTypeName", ""), abbr)
        sqft_str = sqft_str or ""
        # For R&F lookup: fall back to numeric (707U -> 707) when the letter variant isn't in R&F.
        # Display value stays as sqft_str (707U) — only the lookup key is normalized.
        lookup_key = normalize_sqft_key(sqft_str, rf_by_sqft)
        rf = rf_by_sqft.get(lookup_key, {})
        charges = fetch_all_charges(property_id, lease.get("billingAccountId"))
        deposit = fetch_deposit(property_id, lease.get("billingAccountId"))
        rows.append(build_row(lease, sqft_str, renov_idx, rf, charges, deposit, expiring_label))
        via = f' (via {lookup_key})' if lookup_key != sqft_str else ''
        print(f"    {lease.get('unitNumber',''):>6}  {sqft_str:>5}{via:<10} {RENOVATION_LABEL.get(renov_idx,'?'):>3}  RnF={rf.get(RENOVATION_LABEL.get(renov_idx,''),'?')}  Dep={deposit}  charges={charges}")

    if not rows:
        print("  no rows to append")
        return 0

    # Where will they land?
    existing = sheets_client.get_column(tab, "Unit")
    start_row = len(existing) + 2  # +1 for header, +1 for next open row
    end_row = start_row + len(rows) - 1
    print(f"  appending {len(rows)} rows to '{tab}' (rows {start_row}..{end_row})")

    sheets_client.append_rows(tab, rows)
    if propagate_formulas(tab, start_row, end_row):
        print(f"  formulas propagated to rows {start_row}..{end_row}")
    else:
        print(f"  WARNING: formula propagation SKIPPED (set_range not in Apps Script deployment)")
        print(f"           Rows {start_row}..{end_row} have blank formula columns.")
    print("Import done. Running sync to populate F (Renewal Status) + S (Actual Renewal)...\n")

    # Auto-sync: fill F, S, and refresh Lease IDs (idempotent) from ResMan history.
    from sync_renewal_status import run_sync
    run_sync(abbr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
