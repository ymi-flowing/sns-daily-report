"""
Sheet schema constants used across the Renewal Letters tool.

Everything that describes the Google Sheet's structure (column layout, tab
names) lives here. Kept module-level so any script that touches the sheet can
import from one place — no more circular chains through Monday-era files.

Live consumers:
  - `import_to_sheets.py`   (ResMan -> Sheets)
  - `sync_renewal_status.py` (F/S/AI backfill)
  - `letters/generate_letters.py`
  - `Send Letters/send_letters.py`
"""

# Property abbreviation -> actual sheet tab name (only where they differ).
# TRA and AN3 share the TRA-AN3 tab (physically the same building, different LLCs).
TAB_NAMES = {
    "49THSTR": "49thStr",
    "TRA":     "TRA-AN3",
    "AN3":     "TRA-AN3",
}


def tab_name_for(abbr: str) -> str:
    return TAB_NAMES.get(abbr, abbr)


# The 35 canonical column headers, in order. Floorplan (col D) inserted 2026-09-10.
# 49thStr adds a 36th col "Lease ID" (col AJ) — sheet is tolerant of trailing missing cells.
HEADERS = [
    "Expiring Month", "Unit", "Unit Type", "Floorplan", "Renovated", "Resident Name",
    "Renewal Status", "MI Date", "Exp Date", "Months in Property",
    "Base Rent", "Current Total", "RnF Rent", "Adjustment", "Terms A",
    "Rent A", "Total Offer A", "Delta A", "Delta A %",
    "Actual Renewal", "Actual Delta", "Actual Delta %",
    "Utility", "Valet", "Pest", "Amenity", "Internet",
    "Pet", "Parking", "WD", "Storage", "Total Other",
    "Deposit", "R Deposit", "Deposit D",
]
