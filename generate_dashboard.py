"""
SNS Daily Report - Generate HTML Dashboard from ResMan API
"""
import requests
from requests.auth import HTTPBasicAuth
import json
import os
import csv
from io import StringIO
from datetime import datetime, timedelta
from collections import defaultdict
import time

from resman_patch import patch_date_available

AUTO_PUSH_DRY_RUN = os.environ.get("AUTO_PUSH_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def add_business_days(d, n):
    cur = d
    added = 0
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


_auto_push_log = []

# API Credentials (from environment variables, fallback for local dev)
API_KEY = os.environ.get("RESMAN_API_KEY", "43XzRFJkhvAJGLvR6GlJUpKy6nyc")
PARTNER_ID = os.environ.get("RESMAN_PARTNER_ID", "SNSaPi")
ACCOUNT_ID = os.environ.get("RESMAN_ACCOUNT_ID", "1550")
BASE_URL = "https://api.myresman.com"
PARTNERS_API_URL = "https://partners-api.myresman.com"

# Google Sheets - Traffic Data
TRAFFIC_SHEET_URL = "https://docs.google.com/spreadsheets/d/1D8eBjdcClYf9L4DNQg2cKDE1Wuwkc5AOSQcRCPmz18A/export?format=csv&gid=343109953"

# Google Sheets - Financial Data (from n8n automation)
FINANCIAL_INCOME_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQs0vDSTQyN1O3L7uUdHi9c5FHNWZ5lktmKltsiUQmFJ5CWXuiMgirKqz2MXnS2jY2OSNijGppqdb1B/pub?output=csv"
FINANCIAL_DEL_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQs0vDSTQyN1O3L7uUdHi9c5FHNWZ5lktmKltsiUQmFJ5CWXuiMgirKqz2MXnS2jY2OSNijGppqdb1B/pub?output=csv&gid=763378106"

# Property name mapping for Financial data (full names to abbreviations)
FINANCIAL_PROPERTY_MAP = {
    'Arbor Place Apartments': 'AP',
    '49th Street Apartments': '49thStr',
    '49th St Apartments': '49thStr',
    'The Morgan at Ocala': 'MOC',
    'The Morgan At Ocala Apartments': 'MOC',
    'The Mustang at Ocala': 'MUSO',
    'The Vista at Winter Park': 'TV',
    'The Vista At Winter Park': 'TV',
    'The Village at San Jose': 'SJ',
    'Riverside at Orange Park': 'ROP',
    'The Villas at Ortega': 'ORT',
    'The Village at Eastpointe Apts': 'EAST',
    'The Village at Eastpointe Apartments': 'EAST',
}

# Property name mapping (Google Sheet names to abbreviations)
PROPERTY_NAME_MAP = {
    'The Villas at Ortega': 'ORT',
    'The Mustang at Ocala': 'MUSO',
    'The Village at Eastpointe Apts': 'EAST',
    'The Morgan at Ocala': 'MOC',
    'The Village at San Jose': 'SJ',
    '49th Street Apartments': '49thStr',
    'Arbor Place Apartments': 'AP',
    'Riverside at Orange Park': 'ROP',
    'The Vista at Winter Park': 'TV',
}

# Target properties
# Ordered by property size (smallest first) to reduce API load and improve success rate
TARGET_PROPERTIES = ["ROP", "TV", "SJ", "MUSO", "MOC", "EAST", "ORT", "49thStr", "AP"]

# Properties whose traffic comes from the ResMan Prospects API instead of the CRM Google Sheet
RESMAN_TRAFFIC_PROPERTIES = []

# Property grouping by region
PROPERTY_GROUPS = {
    "Tampa": ["49thStr"],
    "Orlando": ["TV", "MOC", "MUSO"],
    "Jacksonville": ["AP", "SJ", "ROP", "ORT"],
    "Ft. Lauderdale": ["EAST"]
}

# API Configuration
API_TIMEOUT = 30  # Standard timeout
API_MAX_RETRIES = 2
API_RETRY_DELAY = 3  # Seconds between retries
API_CALL_DELAY = 0.5  # Small delay between API calls
PROPERTY_REQUEST_DELAY = 1  # Seconds between property requests

def make_partners_api_request(endpoint, params, description="API call"):
    """Make a request to the Partners API with retry logic and increased timeout.

    Args:
        endpoint: API endpoint path (e.g., "/Leasing/Leases")
        params: Query parameters dict
        description: Human-readable description for logging

    Returns:
        Response JSON data or None on failure
    """
    headers = {
        "ResMan-Account-Id": ACCOUNT_ID,
        "Accept": "application/json",
    }

    # Small delay before each API call to avoid rate limiting
    time.sleep(API_CALL_DELAY)

    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            resp = requests.get(
                f"{PARTNERS_API_URL}{endpoint}",
                headers=headers,
                params=params,
                auth=HTTPBasicAuth(PARTNER_ID, API_KEY),
                timeout=API_TIMEOUT
            )

            if resp.ok:
                return resp.json()
            elif resp.status_code == 502:
                # Server overload - wait longer before retry
                print(f"    Warning: {description} returned 502 Bad Gateway (attempt {attempt}/{API_MAX_RETRIES})")
            else:
                print(f"    Warning: {description} returned {resp.status_code} (attempt {attempt}/{API_MAX_RETRIES})")

        except requests.exceptions.Timeout:
            print(f"    Warning: {description} timed out (attempt {attempt}/{API_MAX_RETRIES})")
        except requests.exceptions.RequestException as e:
            print(f"    Warning: {description} failed: {e} (attempt {attempt}/{API_MAX_RETRIES})")

        # Retry with delay (except on last attempt)
        if attempt < API_MAX_RETRIES:
            print(f"    Retrying in {API_RETRY_DELAY}s...")
            time.sleep(API_RETRY_DELAY)

    return None

def get_traffic_data():
    """Fetch traffic data from Google Sheet"""
    try:
        response = requests.get(TRAFFIC_SHEET_URL, timeout=30)
        response.raise_for_status()

        reader = csv.DictReader(StringIO(response.text))
        rows = list(reader)

        # Get current month
        now = datetime.now()
        current_month = now.strftime("%m/%Y")

        # Aggregate by property
        traffic_by_prop = defaultdict(lambda: {"total": 0})

        for r in rows:
            date_str = r.get('Formatted Create Date', '')[:10]  # MM/DD/YYYY
            prop_name = r.get('Property', '')
            abbr = PROPERTY_NAME_MAP.get(prop_name, '')

            if abbr and date_str:
                # Check if this month
                try:
                    parts = date_str.split('/')
                    if len(parts) >= 3:
                        row_month = f"{parts[0]}/{parts[2]}"
                        if row_month == current_month:
                            traffic_by_prop[abbr]["total"] += 1
                except:
                    pass

        # Calculate daily average (per day elapsed in the month, incl. zero-lead days)
        elapsed_days = now.day  # days since the 1st of the current month (inclusive)
        result = {}
        for abbr, data in traffic_by_prop.items():
            result[abbr] = {
                "total": data["total"],
                "daily_avg": round(data["total"] / elapsed_days, 1)
            }

        return result
    except Exception as e:
        print(f"    Warning: Could not fetch traffic data: {e}")
        return {}


def fetch_prospects(property_id):
    """Fetch prospects with first contact since the 1st of the current month.

    Used for properties not present in the CRM Google Sheet (see
    RESMAN_TRAFFIC_PROPERTIES). Returns the list of prospect records (or []).
    """
    first_of_month = datetime.now().replace(day=1).strftime("%Y-%m-%d 00:00:00")
    params = {
        "propertyid": property_id,                 # lowercase, per ResMan /Prospects
        "modifiedSince": "2024-01-01 00:00:00",     # broad; real filter is firstContactDateFrom
        "firstContactDateFrom": first_of_month,
        "includeCommunicationLog": "false",
    }
    data = make_partners_api_request("/Prospects", params, f"Prospects {property_id}")
    return (data or {}).get("prospects", []) or []


def get_resman_traffic_for_property(property_id):
    """Build a traffic entry ({"total", "daily_avg"}) from ResMan prospects.

    Counts ALL prospects (including Lost) with first contact this month. Daily
    average uses days elapsed in the month (matching get_traffic_data()).
    """
    prospects = fetch_prospects(property_id)
    total = len(prospects)
    elapsed_days = datetime.now().day
    return {"total": total, "daily_avg": round(total / elapsed_days, 1)}


def get_receivables_mtd(property_id):
    """Sum of positive account balances (money owed to the property) as of today,
    from ResMan /Transactions/Receivables. This is the 'Receivable MTD' figure
    shown under Net in the Financial Summary card."""
    today = datetime.now().strftime("%Y-%m-%d")
    params = {"propertyId": property_id, "date": today}
    data = make_partners_api_request("/Transactions/Receivables", params, f"Receivables {property_id}")
    accounts = (data or {}).get("accounts", []) or []
    return round(sum((a.get("balance") or 0) for a in accounts if (a.get("balance") or 0) > 0), 2)


def get_financial_data():
    """Fetch financial data from Google Sheet (income and delinquency)"""
    try:
        now = datetime.now()
        timestamp = now.strftime("%b %d, %Y at %I:%M %p")

        # Fetch income data (charges and credits)
        income_response = requests.get(FINANCIAL_INCOME_URL, timeout=30)
        income_response.raise_for_status()
        income_reader = csv.DictReader(StringIO(income_response.text))

        income_data = {}
        for row in income_reader:
            prop_name = row.get('Property', '')
            abbr = FINANCIAL_PROPERTY_MAP.get(prop_name, '')
            if not abbr:
                continue

            category = row.get('Category', '').lower()
            try:
                amount = float(row.get('Amount', 0) or 0)
            except:
                amount = 0

            if abbr not in income_data:
                income_data[abbr] = {'charge': 0, 'credit': 0}

            if 'charge' in category:
                income_data[abbr]['charge'] += amount
            elif 'credit' in category:
                income_data[abbr]['credit'] += amount

        # Fetch delinquency data
        del_response = requests.get(FINANCIAL_DEL_URL, timeout=30)
        del_response.raise_for_status()
        del_reader = csv.DictReader(StringIO(del_response.text))

        del_data = {}
        for row in del_reader:
            prop_name = row.get('Property', '')
            abbr = FINANCIAL_PROPERTY_MAP.get(prop_name, '')
            if not abbr:
                continue

            category = row.get('Category', '').lower()
            try:
                # Clean currency formatting ($, commas)
                total_str = row.get('Total', '0').replace('$', '').replace(',', '')
                total = float(total_str) if total_str else 0
                count = int(row.get('Count', 0) or 0)
            except:
                total = 0
                count = 0

            if abbr not in del_data:
                del_data[abbr] = {
                    'current': {'amount': 0, 'count': 0},
                    'eviction': {'amount': 0, 'count': 0},
                    'formers': {'amount': 0, 'count': 0}
                }

            if 'current' in category:
                del_data[abbr]['current'] = {'amount': total, 'count': count}
            elif 'eviction' in category:
                del_data[abbr]['eviction'] = {'amount': total, 'count': count}
            elif 'former' in category:
                del_data[abbr]['formers'] = {'amount': total, 'count': count}

        return {
            'income': income_data,
            'delinquency': del_data,
            'timestamp': timestamp
        }
    except Exception as e:
        print(f"    Warning: Could not fetch financial data: {e}")
        return {'income': {}, 'delinquency': {}, 'timestamp': ''}


def fetch_leases(property_id):
    """Fetch all leases with history - single API call for all lease processing"""
    # Use 730 days to capture renewed leases (needed for expiring leases)
    since_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    params = {
        "propertyId": property_id,
        "includeLeaseHistory": "true",
        "modifiedSince": since_date
    }

    result = make_partners_api_request("/Leasing/Leases", params, "Leases API")
    return result.get("leases", []) if result else []


def fetch_units_availability(property_id):
    """Fetch unit availability data from Partners API.

    Replaces Legacy GetUnitStatus + Partners /Units with a single call.
    Provides: vacancyStatus, status, availableForOnlineMarketing, unitTypeName,
              occupyingLease.moveOutDate, and more.
    """
    params = {"propertyId": property_id}
    result = make_partners_api_request("/Units/Availability", params, "Units/Availability API")
    return result.get("units", []) if result else []


def fetch_deposit_sum(property_id, billing_account_id):
    """Return sum of depositTransactions.amount for an applicant's ledger.
    Returns 0 on error so the alert conservatively does not fire."""
    if not billing_account_id:
        return 0
    params = {"propertyId": property_id, "billingAccountId": billing_account_id}
    result = make_partners_api_request("/Transactions/DepositLedger", params, "DepositLedger")
    if not result:
        return 0
    return sum((t.get("amount") or 0) for t in result.get("depositTransactions", []))


def get_pending_leases_data(property_id, leases=None, units_availability=None):
    """Get pending lease/applicant data for a property

    Args:
        property_id: Property ID (used if leases not provided)
        leases: Pre-fetched leases data (optional, to avoid duplicate API call)
        units_availability: Pre-fetched Units/Availability list; used to flag
            Holding Units via excludedFromOccupancy for the two leasing alerts.
    """
    try:
        # Use provided leases or fetch
        if leases is None:
            leases = fetch_leases(property_id)

        # Build unit_id -> excludedFromOccupancy lookup for Holding-Unit detection
        holding_by_unit_id = {}
        if units_availability:
            for u in units_availability:
                uid = u.get("unitId")
                if uid:
                    holding_by_unit_id[uid] = bool(u.get("excludedFromOccupancy", False))

        # Filter for Pending status and Pending Transfer (for popup lists)
        # Pending Transfer is included for Vacant Leased/NTV Leased popups but NOT counted as applicants
        pending = [l for l in leases if l.get("leaseStatus") in ["Pending", "Pending Transfer"]]

        # Categorize by approval and signing status (excluding transfers from counts)
        pending_approval = 0
        approved_not_signed = 0
        signed_lease = 0

        # Build detailed applicants list for pop-up
        applicants_list = []
        # Alert lists mutate the applicant row dicts in place so the Applicants
        # popup also shows the alert dot (same pattern as insufficientNoticeList).
        pending_in_real_unit_list = []
        approved_paid_in_holding_candidates = []

        for lease in pending:
            approval_date = lease.get("approvalDate")
            signed_date = lease.get("leaseSignedDate")
            is_transfer = lease.get("leaseStatus") == "Pending Transfer"

            # Determine status (don't count transfers in regular applicant counts)
            if is_transfer:
                status = "Transfer"
            elif signed_date:
                signed_lease += 1
                status = "Signed"
            elif approval_date:
                approved_not_signed += 1
                status = "Approved"
            else:
                pending_approval += 1
                status = "Pending"

            # Get applicant name
            people = lease.get("people", [])
            applicant_name = ""
            if people:
                p = people[0]
                applicant_name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()

            unit_id = lease.get("unitId", "")
            row = {
                "status": status,
                "unit": lease.get("unitNumber", ""),
                "unitId": unit_id,
                "type": lease.get("unitTypeName", ""),
                "applicant": applicant_name,
                "leaseId": lease.get("leaseId", ""),
                "billingAccountId": lease.get("billingAccountId", ""),
                "appDate": (lease.get("applicationDate") or "")[:10],
                "approvedDate": (approval_date or "")[:10] if approval_date else "",
                "signedDate": (signed_date or "")[:10] if signed_date else "",
                "moveIn": (lease.get("scheduledMoveInDate") or lease.get("leaseStartDate") or "")[:10],
                "isTransfer": is_transfer
            }
            applicants_list.append(row)

            # Alert #1: Pending applicant assigned to a non-Holding (real) unit
            if status == "Pending" and unit_id in holding_by_unit_id and holding_by_unit_id[unit_id] is False:
                row["hasAlert"] = True
                row["alertMsg"] = "Pending applicant in a real unit"
                pending_in_real_unit_list.append(row)

            # Alert #2 candidate: Approved applicant still in a Holding Unit
            # Deposit check happens below (one API call per candidate).
            elif status == "Approved" and holding_by_unit_id.get(unit_id) is True:
                approved_paid_in_holding_candidates.append(row)

        # Count new leases signed this month (MTD) - excludes renewals
        # New lease = exactly 1 history entry (the current lease itself)
        # Renewal = 2+ history entries (previous lease + current)
        today = datetime.now()
        first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
        signed_leases_mtd = 0

        for lease in leases:
            signed_date_str = lease.get("leaseSignedDate")
            if not signed_date_str:
                continue

            # Check if signed this month
            if signed_date_str >= first_of_month:
                # Check if it's a new lease (not a renewal)
                history = lease.get("history", [])
                if len(history) == 1:  # New lease has exactly 1 history entry
                    signed_leases_mtd += 1

        # Applicants MTD: applications submitted this month, bucketed by
        # current outcome. Priority: Cancelled > Denied > Approved > Active.
        # Pending Transfers are internal moves, not new applications.
        applicants_mtd = 0
        applicants_mtd_approved = 0
        applicants_mtd_denied = 0
        applicants_mtd_cancelled = 0
        applicants_mtd_active = 0
        applicants_mtd_list = []
        for lease in leases:
            if lease.get("leaseStatus") == "Pending Transfer":
                continue
            app_date = (lease.get("applicationDate") or "")[:10]
            if not app_date or app_date < first_of_month:
                continue
            applicants_mtd += 1
            if lease.get("cancellationDate"):
                applicants_mtd_cancelled += 1
                status = "Cancelled"
            elif lease.get("denialDate"):
                applicants_mtd_denied += 1
                status = "Denied"
            elif lease.get("approvalDate") or lease.get("leaseSignedDate"):
                applicants_mtd_approved += 1
                status = "Approved"
            else:
                applicants_mtd_active += 1
                status = "Still Active"
            people = lease.get("people", [])
            name = ""
            if people:
                p = people[0]
                name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
            applicants_mtd_list.append({
                "status": status,
                "unit": lease.get("unitNumber", ""),
                "unitId": lease.get("unitId", ""),
                "type": lease.get("unitTypeName", ""),
                "applicant": name,
                "leaseId": lease.get("leaseId", ""),
                "appDate": app_date,
                "approvedDate": (lease.get("approvalDate") or "")[:10],
                "denialDate": (lease.get("denialDate") or "")[:10],
                "cancellationDate": (lease.get("cancellationDate") or "")[:10],
                "signedDate": (lease.get("leaseSignedDate") or "")[:10],
            })

        # Alert #2: Approved + deposit paid, still in a Holding Unit.
        # One DepositLedger call per candidate (billingAccountId isolates the applicant).
        approved_paid_in_holding_list = []
        for row in approved_paid_in_holding_candidates:
            deposit_sum = fetch_deposit_sum(property_id, row.get("billingAccountId"))
            if deposit_sum > 0:
                row["hasAlert"] = True
                row["alertMsg"] = "Approved + deposit paid, still in Holding Unit"
                approved_paid_in_holding_list.append(row)

        # Total applicants excludes transfers (they're only for Vacant/NTV Leased popups)
        total_applicants = pending_approval + approved_not_signed + signed_lease

        return {
            "total_applicants": total_applicants,
            "pending_approval": pending_approval,
            "approved": approved_not_signed,
            "signed_lease": signed_lease,
            "signed_leases_mtd": signed_leases_mtd,
            "applicants_mtd": applicants_mtd,
            "applicants_mtd_approved": applicants_mtd_approved,
            "applicants_mtd_denied": applicants_mtd_denied,
            "applicants_mtd_cancelled": applicants_mtd_cancelled,
            "applicants_mtd_active": applicants_mtd_active,
            "applicantsList": sorted(applicants_list, key=lambda x: x["unit"]),
            "applicantsMtdList": sorted(applicants_mtd_list, key=lambda x: (x["appDate"], x["unit"]), reverse=True),
            "pendingInRealUnitList": sorted(pending_in_real_unit_list, key=lambda x: x["unit"]),
            "approvedPaidInHoldingList": sorted(approved_paid_in_holding_list, key=lambda x: x["unit"]),
        }
    except Exception as e:
        print(f"    Warning: Could not fetch lease data: {e}")
        return {"total_applicants": 0, "pending_approval": 0, "approved": 0, "signed_lease": 0, "signed_leases_mtd": 0, "applicants_mtd": 0, "applicants_mtd_approved": 0, "applicants_mtd_denied": 0, "applicants_mtd_cancelled": 0, "applicants_mtd_active": 0, "applicantsList": [], "applicantsMtdList": [], "pendingInRealUnitList": [], "approvedPaidInHoldingList": []}

def get_expiring_leases_data(property_id, leases=None, units_availability=None):
    """Get expiring leases data for current + next 3 months

    Args:
        property_id: Property ID (used if leases not provided)
        leases: Pre-fetched leases data (optional, to avoid duplicate API call)
        units_availability: Pre-fetched Units/Availability list; used to source
            noticeToVacateDate per unit (not available on /Leasing/Leases).

    Includes:
    - Current leases with leaseEndDate in range
    - Renewed leases found in history (already renewed to next year)
    - Former residents if MO date is within 3 months (counted in NTV/Formers)
    - Evicted residents if MO date is within 3 months (counted in NTV/Formers)
    """
    try:
        # Use provided leases or fetch
        if leases is None:
            leases = fetch_leases(property_id)

        if not leases:
            return {"month1": {}, "month2": {}, "month3": {}, "month4": {}, "total": {}}
        today = datetime.now().date()

        # Build unit -> noticeToVacateDate / moveOutDate maps from Units/Availability.
        # The /Leasing/Leases response leaves moveOutDate blank on active NTV leases
        # (that field only populates once the resident actually moves out), so we
        # fall back to the occupying-lease MO date on the unit for NTV rows.
        ntv_by_unit = {}
        mo_by_unit = {}
        if units_availability:
            for u in units_availability:
                u_num = u.get("number", "")
                occ = u.get("occupyingLease") or {}
                ntv = (occ.get("noticeToVacateDate") or "")[:10]
                mo = (occ.get("moveOutDate") or "")[:10]
                if u_num and ntv:
                    ntv_by_unit[u_num] = ntv
                if u_num and mo:
                    mo_by_unit[u_num] = mo

        # Define month boundaries - current + next 3 months (4 total)
        current_month = today.month
        current_year = today.year

        def get_month_range(year, month):
            start = datetime(year, month, 1).date()
            if month == 12:
                end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
            else:
                end = datetime(year, month + 1, 1).date() - timedelta(days=1)
            return start, end

        def next_month(year, month):
            if month == 12:
                return year + 1, 1
            return year, month + 1

        def prev_months(year, month, n):
            """Go back n months"""
            for _ in range(n):
                if month == 1:
                    year -= 1
                    month = 12
                else:
                    month -= 1
            return year, month

        # Calculate 4 month ranges - Month 1 starts from 1st of current month
        m1_start, m1_end = get_month_range(current_year, current_month)
        y2, m2 = next_month(current_year, current_month)
        m2_start, m2_end = get_month_range(y2, m2)
        y3, m3 = next_month(y2, m2)
        m3_start, m3_end = get_month_range(y3, m3)
        y4, m4 = next_month(y3, m3)
        m4_start, m4_end = get_month_range(y4, m4)

        # Calculate MO date windows for Formers (3 months back from each month)
        mo_y1, mo_m1 = prev_months(current_year, current_month, 3)
        mo_window_1_start = datetime(mo_y1, mo_m1, 1).date()
        mo_window_1_end = m1_end

        mo_y2, mo_m2 = prev_months(y2, m2, 3)
        mo_window_2_start = datetime(mo_y2, mo_m2, 1).date()
        mo_window_2_end = m2_end

        mo_y3, mo_m3 = prev_months(y3, m3, 3)
        mo_window_3_start = datetime(mo_y3, mo_m3, 1).date()
        mo_window_3_end = m3_end

        mo_y4, mo_m4 = prev_months(y4, m4, 3)
        mo_window_4_start = datetime(mo_y4, mo_m4, 1).date()
        mo_window_4_end = m4_end

        # Initialize counters - NTV/Formers combines NTV + Former with recent MO
        statuses = ["Current", "Pending Renewal", "Renewed", "NTV/Formers", "Under Eviction"]
        month1 = {s: 0 for s in statuses}
        month2 = {s: 0 for s in statuses}
        month3 = {s: 0 for s in statuses}
        month4 = {s: 0 for s in statuses}
        total = {s: 0 for s in statuses}

        # Detailed lists for pop-ups (by month)
        expiring_lists = {
            "month1": [],
            "month2": [],
            "month3": [],
            "month4": []
        }

        # Track processed unit+endDate combos to avoid duplicates
        processed = set()

        def get_month_bucket(end_date):
            """Return month bucket number (1-4) or None if not in range"""
            if m1_start <= end_date <= m1_end:
                return 1
            elif m2_start <= end_date <= m2_end:
                return 2
            elif m3_start <= end_date <= m3_end:
                return 3
            elif m4_start <= end_date <= m4_end:
                return 4
            return None

        def is_mo_in_window(mo_date, month_bucket):
            """Check if MO date is within 3-month window for a month"""
            if mo_date is None:
                return False
            if month_bucket == 1:
                return mo_window_1_start <= mo_date <= mo_window_1_end
            elif month_bucket == 2:
                return mo_window_2_start <= mo_date <= mo_window_2_end
            elif month_bucket == 3:
                return mo_window_3_start <= mo_date <= mo_window_3_end
            elif month_bucket == 4:
                return mo_window_4_start <= mo_date <= mo_window_4_end
            return False

        def add_to_bucket(bucket, status, detail_entry=None):
            """Add a count to the appropriate month bucket and track details"""
            if bucket == 1:
                month1[status] += 1
                if detail_entry:
                    expiring_lists["month1"].append(detail_entry)
            elif bucket == 2:
                month2[status] += 1
                if detail_entry:
                    expiring_lists["month2"].append(detail_entry)
            elif bucket == 3:
                month3[status] += 1
                if detail_entry:
                    expiring_lists["month3"].append(detail_entry)
            elif bucket == 4:
                month4[status] += 1
                if detail_entry:
                    expiring_lists["month4"].append(detail_entry)
            total[status] += 1

        # Process each lease
        for lease in leases:
            unit = lease.get("unitNumber", "")
            lease_status = lease.get("leaseStatus", "")
            history = lease.get("history", [])
            unit_type = lease.get("unitTypeName", "")
            lease_id = lease.get("leaseId", "")
            # Get resident name from people array
            people = lease.get("people", [])
            resident_name = ""
            if people:
                first = people[0].get("firstName", "") or ""
                last = people[0].get("lastName", "") or ""
                resident_name = f"{first} {last}".strip()

            # Get move out date for Formers
            mo_date = None
            mo_date_str = lease.get("moveOutDate", "")
            if mo_date_str:
                try:
                    mo_date = datetime.strptime(mo_date_str[:10], "%Y-%m-%d").date()
                except:
                    pass

            # PART 1: Check current leaseEndDate
            end_date_str = lease.get("leaseEndDate", "")
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str[:10], "%Y-%m-%d").date()
                    bucket = get_month_bucket(end_date)
                    if bucket:
                        key = (unit, end_date_str[:10])
                        if key not in processed:
                            processed.add(key)

                            # Check for Pending Renewal in history
                            pending_renewal = None
                            for h in history:
                                if h.get("status") == "Pending Renewal":
                                    pending_renewal = h
                                    break

                            # Build detail entry for pop-up. Prefer the lease's
                            # own moveOutDate; fall back to the planned MO date
                            # on the occupying lease (NTV residents).
                            mo_date_display = (mo_date_str[:10] if mo_date_str else "") or mo_by_unit.get(unit, "")
                            detail_entry = {
                                "unit": unit,
                                "unitId": lease.get("unitId", ""),
                                "type": unit_type,
                                "resident": resident_name,
                                "leaseId": lease_id,
                                "leaseEnd": end_date_str[:10],
                                "moDate": mo_date_display,
                                "renewalSigned": "",
                                "ntvDate": ntv_by_unit.get(unit, ""),
                                "isEarlyTermination": False,
                            }

                            # Determine status
                            if pending_renewal:
                                if pending_renewal.get("signedDate"):
                                    detail_entry["status"] = "Renewed"
                                    detail_entry["renewalSigned"] = (pending_renewal.get("signedDate") or "")[:10]
                                    add_to_bucket(bucket, "Renewed", detail_entry)
                                else:
                                    detail_entry["status"] = "Pending Renewal"
                                    add_to_bucket(bucket, "Pending Renewal", detail_entry)
                            elif lease_status == "Notice to Vacate":
                                detail_entry["status"] = "NTV"
                                detail_entry["isEarlyTermination"] = bool(
                                    detail_entry["moDate"] and detail_entry["moDate"] < end_date_str[:10]
                                )
                                add_to_bucket(bucket, "NTV/Formers", detail_entry)
                            elif lease_status == "Under Eviction":
                                detail_entry["status"] = "Under Eviction"
                                add_to_bucket(bucket, "Under Eviction", detail_entry)
                            elif lease_status == "Current":
                                detail_entry["status"] = "Current"
                                add_to_bucket(bucket, "Current", detail_entry)
                            elif lease_status == "Former":
                                if is_mo_in_window(mo_date, bucket):
                                    detail_entry["status"] = "Former"
                                    detail_entry["isEarlyTermination"] = bool(
                                        detail_entry["moDate"] and detail_entry["moDate"] < end_date_str[:10]
                                    )
                                    add_to_bucket(bucket, "NTV/Formers", detail_entry)
                            elif lease_status == "Evicted":
                                if is_mo_in_window(mo_date, bucket):
                                    detail_entry["status"] = "Evicted"
                                    add_to_bucket(bucket, "NTV/Formers", detail_entry)
                            # Skip Cancelled, Denied, etc.
                except:
                    pass

            # PART 2: Check history for "Renewed" entries (already renewed to next year)
            for h in history:
                h_end_str = h.get("endDate", "")
                h_status = h.get("status", "")
                if h_end_str and h_status == "Renewed":
                    try:
                        h_end_date = datetime.strptime(h_end_str[:10], "%Y-%m-%d").date()
                        bucket = get_month_bucket(h_end_date)
                        if bucket:
                            key = (unit, h_end_str[:10])
                            if key not in processed:
                                processed.add(key)
                                detail_entry = {
                                    "status": "Renewed",
                                    "unit": unit,
                                    "unitId": lease.get("unitId", ""),
                                    "type": unit_type,
                                    "resident": resident_name,
                                    "leaseId": lease_id,
                                    "leaseEnd": h_end_str[:10],
                                    "moDate": "",
                                    "renewalSigned": (h.get("signedDate") or "")[:10],
                                    "ntvDate": ntv_by_unit.get(unit, ""),
                                }
                                add_to_bucket(bucket, "Renewed", detail_entry)
                    except:
                        pass

        # Sort expiring lists by unit
        for month_key in expiring_lists:
            expiring_lists[month_key] = sorted(expiring_lists[month_key], key=lambda x: x["unit"])

        # Build Insufficient-Notice list: Current or Pending Renewal rows whose
        # lease ends in <60 days and have no NTV filed. Mutate the shared row
        # dicts so the Expiring popup also shows the alert dot.
        insufficient_notice_list = []
        for month_key in expiring_lists:
            for row in expiring_lists[month_key]:
                if row.get("status") not in ("Current", "Pending Renewal"):
                    continue
                le = row.get("leaseEnd") or ""
                try:
                    le_dt = datetime.strptime(le[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                days_left = (le_dt - today).days
                if days_left >= 60 or days_left < 0:
                    continue
                row["hasAlert"] = True
                row["daysLeft"] = days_left
                if row.get("status") == "Pending Renewal":
                    row["alertMsg"] = f"Lease ends in {days_left}d — renewal not signed"
                else:
                    row["alertMsg"] = f"Lease ends in {days_left}d — no NTV filed"
                insufficient_notice_list.append(row)

        insufficient_notice_list.sort(key=lambda r: (r.get("daysLeft", 999), r.get("unit", "")))

        # Add month labels
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return {
            "month1": month1,
            "month1_label": month_names[current_month - 1],
            "month2": month2,
            "month2_label": month_names[m2 - 1],
            "month3": month3,
            "month3_label": month_names[m3 - 1],
            "month4": month4,
            "month4_label": month_names[m4 - 1],
            "total": total,
            "expiringLists": expiring_lists,
            "insufficientNoticeList": insufficient_notice_list,
        }
    except Exception as e:
        print(f"    Warning: Could not fetch expiring leases: {e}")
        return {"month1": {}, "month2": {}, "month3": {}, "month4": {}, "total": {}}


def write_renewal_summary(leases_data, output_path):
    """Write current-month renewal summary as JSON for n8n end-of-month email.

    Renewal % formula matches the dashboard header (generate_dashboard.py ~3021):
    renewed / (current + renewed + ntvFormers + underEviction) * 100 — excludes
    Pending Renewal from the denominator.
    """
    from zoneinfo import ZoneInfo
    florida_tz = ZoneInfo("America/New_York")
    now = datetime.now(florida_tz)
    month_key = now.strftime("%Y-%m")
    month_label = now.strftime("%B %Y")

    def bucket_row(month1):
        current = month1.get("Current", 0)
        pending = month1.get("Pending Renewal", 0)
        renewed = month1.get("Renewed", 0)
        ntv_formers = month1.get("NTV/Formers", 0)
        under_eviction = month1.get("Under Eviction", 0)
        total = current + pending + renewed + ntv_formers + under_eviction
        denom = current + renewed + ntv_formers + under_eviction
        pct = round(renewed / denom * 100, 1) if denom > 0 else 0.0
        return {
            "current": current,
            "pendingRenewal": pending,
            "renewed": renewed,
            "ntvFormers": ntv_formers,
            "underEviction": under_eviction,
            "total": total,
            "renewalPct": pct,
        }

    properties = {}
    portfolio_agg = {"Current": 0, "Pending Renewal": 0, "Renewed": 0,
                     "NTV/Formers": 0, "Under Eviction": 0}
    for abbr in TARGET_PROPERTIES:
        month1 = (leases_data.get(abbr, {}).get("expiring") or {}).get("month1") or {}
        properties[abbr] = bucket_row(month1)
        for k in portfolio_agg:
            portfolio_agg[k] += month1.get(k, 0)

    payload = {
        "month": month_key,
        "monthLabel": month_label,
        "generatedAt": now.isoformat(timespec="seconds"),
        "properties": properties,
        "portfolio": bucket_row(portfolio_agg),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def fetch_partners_units(property_id):
    """Fetch units from Partners API - single call for renovation and off-market"""
    params = {"propertyId": property_id}
    result = make_partners_api_request("/Units", params, "Units API")
    return result.get("units", []) if result else []


def get_units_renovation_data(property_id, units_availability=None):
    """Get unit renovation status breakdown

    Args:
        property_id: Property ID (used if units_availability not provided)
        units_availability: Pre-fetched units from Units/Availability API
    """
    try:
        # Use provided units or fetch
        if units_availability is None:
            units_availability = fetch_units_availability(property_id)

        # Filter to only units included in occupancy (exclude holding/office units)
        units = [u for u in units_availability if not u.get("excludedFromOccupancy", False)]

        # Count by renovation status (parsed from unitTypeName field)
        renovated = 0
        partial = 0
        classic = 0

        for unit in units:
            # Units/Availability uses unitTypeName instead of unitType
            unit_type = unit.get("unitTypeName", "").lower()

            # Check for renovation status in unit type name
            if "partial renovated" in unit_type:
                partial += 1
            elif "renovated" in unit_type:
                renovated += 1
            elif "classic" in unit_type:
                classic += 1
            else:
                # Default to classic if no status found
                classic += 1

        total = renovated + partial + classic

        return {
            "renovated": renovated,
            "partial": partial,
            "classic": classic,
            "total": total,
            "renovated_pct": round(renovated / total * 100, 1) if total else 0,
            "partial_pct": round(partial / total * 100, 1) if total else 0,
            "classic_pct": round(classic / total * 100, 1) if total else 0
        }
    except Exception as e:
        print(f"    Warning: Could not fetch units data: {e}")
        return {"renovated": 0, "partial": 0, "classic": 0, "total": 0, "renovated_pct": 0, "partial_pct": 0, "classic_pct": 0}


def get_work_orders_data(property_id):
    """Get open work orders count (excluding make-ready) and new in last 24h"""
    # Fetch work orders from past 90 days
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    params = {
        "propertyId": property_id,
        "startDate": start_date
    }

    result = make_partners_api_request("/WorkOrders", params, "WorkOrders API")
    if not result:
        return {"open_total": 0, "new_24h": 0}

    work_orders = result.get("workOrders", [])

    # Filter: open statuses AND NOT make-ready
    open_statuses = ["Submitted", "Not Started", "In Progress", "Scheduled", "On Hold"]
    open_wos = [wo for wo in work_orders
                if wo.get("status") in open_statuses
                and wo.get("isMakeReady") == False]

    # Count new in last 24 hours
    now = datetime.now()
    yesterday = now - timedelta(hours=24)
    new_24h = 0
    for wo in open_wos:
        reported_str = wo.get("reportedDate", "")
        if reported_str:
            try:
                reported = datetime.fromisoformat(reported_str.replace("Z", "+00:00").split("+")[0])
                if reported >= yesterday:
                    new_24h += 1
            except:
                pass

    return {
        "open_total": len(open_wos),
        "new_24h": new_24h
    }


def get_future_moves_data(property_id, leases=None, units_availability=None):
    """Get future move-ins and move-outs for the current month.

    Future Move-Ins: Signed pending leases with leaseStartDate in current month
    Future Move-Outs: NTV leases with MoveOutDate in current month (from today onwards)

    Args:
        property_id: Property ID (used if leases not provided)
        leases: Pre-fetched leases data (optional, to avoid duplicate API call)
        units_availability: Pre-fetched units from Units/Availability API (for moveOutDate)
    """
    try:
        # Use provided leases or fetch
        if leases is None:
            leases = fetch_leases(property_id)

        if not leases:
            return {"future_mi": 0, "future_mo": 0, "month_label": ""}

        # Build lookup for MoveOutDate from Units/Availability (occupyingLease.moveOutDate)
        # This is the actual scheduled move-out date, which may differ from leaseEndDate
        move_out_dates = {}
        if units_availability:
            for unit in units_availability:
                unit_num = unit.get("number", "")
                occ_lease = unit.get("occupyingLease")
                if occ_lease and unit_num:
                    mo_date = occ_lease.get("moveOutDate", "")
                    if mo_date:
                        move_out_dates[unit_num] = mo_date

        today = datetime.now().date()

        # Calculate end of current month
        current_year = today.year
        current_month = today.month
        if current_month == 12:
            end_of_month = datetime(current_year + 1, 1, 1).date() - timedelta(days=1)
        else:
            end_of_month = datetime(current_year, current_month + 1, 1).date() - timedelta(days=1)

        # Month label for display (e.g., "May 2026")
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        month_label = f"{month_names[current_month - 1]} {current_year}"

        future_mi = 0
        future_mo = 0

        # Detailed lists for pop-ups
        future_mi_list = []
        future_mo_list = []

        # Track unique units to avoid double-counting
        mi_units = set()
        mo_units = set()

        # Build unit type and unitId lookup from units_availability
        unit_type_map = {}
        unit_id_map = {}
        if units_availability:
            for u in units_availability:
                unit_num = u.get("number", "")
                if unit_num:
                    unit_type_map[unit_num] = u.get("unitTypeName", "")
                    unit_id_map[unit_num] = u.get("unitId", "")

        for lease in leases:
            unit = lease.get("unitNumber", "")
            lease_status = lease.get("leaseStatus", "")
            signed_date = lease.get("leaseSignedDate")
            start_date_str = lease.get("leaseStartDate", "")
            end_date_str = lease.get("leaseEndDate", "")
            move_in_date = lease.get("moveInDate")  # null if not yet moved in
            unit_type = lease.get("unitTypeName", "") or unit_type_map.get(unit, "")
            lease_id = lease.get("leaseId", "")
            # Get resident name from people array
            people = lease.get("people", [])
            resident_name = ""
            if people:
                first = people[0].get("firstName", "") or ""
                last = people[0].get("lastName", "") or ""
                resident_name = f"{first} {last}".strip()

            # Future Move-Ins: Signed pending leases with start date in current month
            if lease_status == "Pending" and signed_date and start_date_str:
                # Only count if not yet moved in (moveInDate is null)
                if move_in_date is None:
                    try:
                        start_date = datetime.strptime(start_date_str[:10], "%Y-%m-%d").date()
                        # Start date is from today to end of month
                        if today <= start_date <= end_of_month:
                            if unit not in mi_units:
                                mi_units.add(unit)
                                future_mi += 1
                                future_mi_list.append({
                                    "unit": unit,
                                    "unitId": lease.get("unitId", "") or unit_id_map.get(unit, ""),
                                    "type": unit_type,
                                    "resident": resident_name,
                                    "leaseId": lease_id,
                                    "signed": (signed_date or "")[:10],
                                    "moveIn": start_date_str[:10]
                                })
                    except:
                        pass

            # Future Move-Outs: NTV leases with MoveOutDate in current month
            # Use MoveOutDate from Units/Availability (preferred) or fall back to leaseEndDate
            if lease_status == "Notice to Vacate":
                mo_date_str = move_out_dates.get(unit, "") or end_date_str
                if mo_date_str:
                    try:
                        mo_date = datetime.strptime(mo_date_str[:10], "%Y-%m-%d").date()
                        # Move-out date is from today to end of month
                        if today <= mo_date <= end_of_month:
                            if unit not in mo_units:
                                mo_units.add(unit)
                                future_mo += 1
                                # Get NTV date and unitId from units_availability
                                ntv_date = ""
                                unit_id = unit_id_map.get(unit, "")
                                if units_availability:
                                    for u in units_availability:
                                        if u.get("number") == unit:
                                            occ = u.get("occupyingLease", {}) or {}
                                            ntv_date = (occ.get("noticeToVacateDate") or "")[:10]
                                            if not unit_id:
                                                unit_id = u.get("unitId", "")
                                            break
                                future_mo_list.append({
                                    "unit": unit,
                                    "unitId": unit_id,
                                    "type": unit_type,
                                    "resident": resident_name,
                                    "leaseId": lease_id,
                                    "ntvDate": ntv_date,
                                    "moDate": mo_date_str[:10]
                                })
                    except:
                        pass

        return {
            "future_mi": future_mi,
            "future_mo": future_mo,
            "month_label": month_label,
            "futureMIList": sorted(future_mi_list, key=lambda x: x["unit"]),
            "futureMOList": sorted(future_mo_list, key=lambda x: x["unit"])
        }
    except Exception as e:
        print(f"    Warning: Could not calculate future moves: {e}")
        return {"future_mi": 0, "future_mo": 0, "month_label": ""}


def get_off_market_count(property_id, eviction_units=None, units_availability=None, debug=False):
    """Get count of vacant/NTV units off the market (availableForOnlineMarketing = false)
    Only counts non-leased units: Vacant, Notice to Vacate (excludes Pre-Leased)
    Excludes: Occupied, Under Eviction, Down/Abated

    Args:
        property_id: Property ID
        eviction_units: Set of unit numbers under eviction
        units_availability: Pre-fetched units from Units/Availability API
        debug: Print debug info
    """
    try:
        if eviction_units is None:
            eviction_units = set()

        # Use provided units_availability or fetch
        if units_availability is None:
            units_availability = fetch_units_availability(property_id)

        # Count off-market units that are Vacant or NTV but NOT pre-leased
        # Only: "Vacant", "Notice to Vacate" (exclude Pre-Leased variants)
        non_leased_statuses = ["Vacant", "Notice to Vacate"]
        down_statuses = ["Abated", "Down"]

        off_market_count = 0
        off_market_units = []
        available_units = []

        for u in units_availability:
            # Units/Availability uses lowercase field names
            unit_num = u.get("number", "")
            vacancy_status = u.get("vacancyStatus", "")
            ready_status = u.get("status", "")
            excluded = u.get("excludedFromOccupancy", False)
            available_for_marketing = u.get("availableForOnlineMarketing", True)

            # Skip excluded or Down/Abated units
            if excluded or ready_status in down_statuses:
                continue

            # Skip units under eviction
            if unit_num in eviction_units:
                continue

            # Only count non-leased vacant/NTV units
            if vacancy_status not in non_leased_statuses:
                continue

            available_units.append((unit_num, vacancy_status, ready_status))

            # Check if this unit is off market
            if not available_for_marketing:
                off_market_count += 1
                off_market_units.append((unit_num, vacancy_status, ready_status))

        if debug:
            print(f"      DEBUG: Eviction units excluded: {len(eviction_units)}")
            print(f"      DEBUG: Available for rent (non-leased): {len(available_units)}")
            print(f"      DEBUG: Off market: {off_market_count}")
            if off_market_units:
                print(f"      DEBUG: Off market units: {[u[0] for u in off_market_units[:10]]}{'...' if len(off_market_units) > 10 else ''}")

        return off_market_count
    except Exception as e:
        print(f"    Warning: Could not fetch off-market data: {e}")
        return 0


def _ntv_alert_and_push(property_id, unit_id, unit_num, unit_type, mo_date, av_date, today):
    """Compute NTV alert text; auto-PATCH DateAvailable to MO + 10 business days
    when the gap is misaligned (and MO is not past). Returns
    (has_alert, msg, auto_pushed_dict_or_none)."""
    msg = ""
    has_alert = False
    auto_pushed = None

    mo_dt = None
    if mo_date:
        try:
            mo_dt = datetime.strptime(mo_date, "%Y-%m-%d").date()
        except ValueError:
            mo_dt = None

    if mo_dt is not None:
        if mo_dt < today:
            has_alert = True
            msg = "Past Move-Out Date"
        elif av_date:
            try:
                av_dt = datetime.strptime(av_date, "%Y-%m-%d").date()
            except ValueError:
                av_dt = None
            if av_dt is not None:
                min_ready = add_business_days(mo_dt, 10)
                max_ready = add_business_days(mo_dt, 20)
                misaligned = av_dt < min_ready or av_dt > max_ready
                if misaligned:
                    days_gap = (av_dt - mo_dt).days
                    if av_dt < min_ready:
                        msg = (f"Available {abs(days_gap)} days BEFORE move-out"
                               if days_gap < 0 else f"Only {days_gap} days to get ready")
                    else:
                        msg = f"Available {days_gap} days AFTER move-out"
                    has_alert = True
                    target = min_ready
                    if AUTO_PUSH_DRY_RUN:
                        auto_pushed = {
                            "applied": False,
                            "dryRun": True,
                            "newAvDate": target.isoformat(),
                            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        }
                        _auto_push_log.append(
                            f"  [DRY-RUN] {unit_num}: would push avDate {av_date} -> {target.isoformat()}"
                        )
                    else:
                        ok, err = patch_date_available(
                            property_id, unit_id, target.isoformat(),
                            description=f"{unit_num} auto-push"
                        )
                        if ok:
                            auto_pushed = {
                                "applied": True,
                                "newAvDate": target.isoformat(),
                                "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                            }
                            _auto_push_log.append(
                                f"  AUTO-PUSH {unit_num}: avDate -> {target.isoformat()} (was {av_date})"
                            )
                        else:
                            _auto_push_log.append(
                                f"  AUTO-PUSH {unit_num}: FAILED ({err})"
                            )

    if unit_type and "classic" in unit_type.lower():
        msg = (msg + " • " if has_alert else "") + "Classic Unit - please update the unit type"
        has_alert = True

    return has_alert, msg, auto_pushed


def make_request(endpoint, extra_params=None):
    """Make authenticated request to ResMan API"""
    url = f"{BASE_URL}/{endpoint}"
    payload = {
        "IntegrationPartnerID": PARTNER_ID,
        "APIKey": API_KEY,
        "AccountID": ACCOUNT_ID
    }
    if extra_params:
        payload.update(extra_params)
    response = requests.post(url, json=payload, headers={"Accept": "application/json"})
    if response.ok:
        data = response.json()
        if data.get("Status") not in ["Failed", "Error"]:
            return data
    return None

def get_property_data(property_id, property_name, units_availability=None, leases=None):
    """Get unit status data for a property using Units/Availability endpoint.

    Args:
        property_id: Property ID
        property_name: Property name for logging
        units_availability: Pre-fetched units from Units/Availability API
        leases: Pre-fetched leases from Leasing/Leases API (for eviction/former status)
    """
    units = units_availability or []
    leases = leases or []

    # Get today's date for alert calculations
    today = datetime.now().date()

    # Build eviction units set and details from leases (leaseStatus = "Under Eviction")
    eviction_units = set()
    eviction_details = {}  # unit -> {name, leaseEnd, moDate}
    for lease in leases:
        if lease.get("leaseStatus") == "Under Eviction":
            unit_num = lease.get("unitNumber", "")
            eviction_units.add(unit_num)
            people = lease.get("people", [])
            name = ""
            if people:
                p = people[0]
                name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
            eviction_details[unit_num] = {
                "name": name,
                "leaseEnd": (lease.get("leaseEndDate") or "")[:10],
                "moDate": (lease.get("moveOutDate") or "")[:10],
                "leaseId": lease.get("leaseId", "")
            }

    # Build leaseId -> leaseStatus map (to check if occupying lease is "Former")
    lease_status_by_id = {}
    # Build unit -> latest past moveOutDate map (for Days Vacant on Vacant units).
    # Scan every lease with a past MO date — not just leaseStatus=Former — because
    # the most recent lease on a now-vacant unit sometimes still reads Transferred
    # or similar until ResMan ticks it to Former.
    today_iso = today.isoformat()
    last_mo_by_unit = {}
    for lease in leases:
        lease_id = lease.get("leaseId", "")
        if lease_id:
            lease_status_by_id[lease_id] = lease.get("leaseStatus", "")
        u_num = lease.get("unitNumber", "")
        mo = (lease.get("moveOutDate") or "")[:10]
        if not (u_num and mo and mo <= today_iso):
            continue
        prev = last_mo_by_unit.get(u_num)
        if not prev or mo > prev:
            last_mo_by_unit[u_num] = mo

    def _days_vacant(unit_num, fallback_mo=""):
        mo = last_mo_by_unit.get(unit_num) or (fallback_mo or "")[:10]
        if not mo:
            return "", None
        try:
            mo_dt = datetime.strptime(mo, "%Y-%m-%d").date()
        except ValueError:
            return "", None
        days = (today - mo_dt).days
        if days < 0:
            return mo, 0
        return mo, days

    # Build unit -> unit data lookup for quick access
    unit_data_map = {}
    for u in units:
        unit_num = u.get("number", "")
        if unit_num:
            unit_data_map[unit_num] = u

    # Helper to format unit type (shorten if needed)
    def format_type(unit_type_name):
        if not unit_type_name:
            return ""
        return unit_type_name

    # Categorize units based on vacancyStatus and status
    # Track both combined categories and leased subcategories
    # Now storing detailed objects instead of just unit numbers
    status_summary = defaultdict(list)
    detailed_lists = {
        "vacantReadyList": [],
        "vacantNotReadyList": [],
        "ntvList": [],
        "evictionList": [],
        "offMarketList": []
    }

    for u in units:
        # Units/Availability uses lowercase field names
        unit_num = u.get("number", "")
        vacancy_status = u.get("vacancyStatus", "")
        ready_status = u.get("status", "")  # Ready, Not Ready, Down, Abated
        excluded = u.get("excludedFromOccupancy", False)
        is_ready = ready_status == "Ready"
        is_down = ready_status in ["Abated", "Down"]
        unit_type = format_type(u.get("unitTypeName", ""))
        unit_id = u.get("unitId", "")
        av_date = (u.get("dateAvailable") or "")[:10]
        occ_lease = u.get("occupyingLease", {}) or {}
        ntv_date = (occ_lease.get("noticeToVacateDate") or "")[:10]
        mo_date = (occ_lease.get("moveOutDate") or "")[:10]
        available_for_marketing = u.get("availableForOnlineMarketing", True)

        # Skip excluded or Down/Abated units entirely (don't count in totals)
        if excluded or is_down:
            continue

        # Track off-market units (available for rent but not marketed)
        if not available_for_marketing and vacancy_status != "Occupied":
            detailed_lists["offMarketList"].append({
                "unit": unit_num,
                "unitId": unit_id,
                "type": unit_type,
                "vacancyStatus": vacancy_status,
                "avDate": av_date
            })

        # Check for eviction first (takes priority)
        if unit_num in eviction_units:
            status_summary["Under Eviction"].append(unit_num)
            ev_info = eviction_details.get(unit_num, {})
            detailed_lists["evictionList"].append({
                "unit": unit_num,
                "unitId": unit_id,
                "type": unit_type,
                "name": ev_info.get("name", ""),
                "leaseId": ev_info.get("leaseId", ""),
                "leaseEnd": ev_info.get("leaseEnd", ""),
                "moDate": ev_info.get("moDate", "")
            })
        elif vacancy_status == "Occupied":
            status_summary["Occupied"].append(unit_num)
        elif vacancy_status == "Vacant":
            # Not leased vacant
            if is_ready:
                status_summary["Vacant Ready"].append(unit_num)
                _mo_v, _dv = _days_vacant(unit_num, mo_date)
                detailed_lists["vacantReadyList"].append({
                    "unit": unit_num,
                    "unitId": unit_id,
                    "type": unit_type,
                    "avDate": av_date,
                    "lastMoDate": _mo_v,
                    "daysVacant": _dv
                })
            else:
                status_summary["Vacant Not Ready"].append(unit_num)
                # Check for alert: available date in the past
                vnr_alert = False
                vnr_alert_msg = ""
                if av_date:
                    try:
                        av_dt = datetime.strptime(av_date, "%Y-%m-%d").date()
                        if av_dt < today:
                            vnr_alert = True
                            vnr_alert_msg = "Past Available Date"
                    except: pass
                if unit_type and 'classic' in unit_type.lower():
                    vnr_alert_msg = (vnr_alert_msg + " • " if vnr_alert else "") + "Classic Unit - please update the unit type"
                    vnr_alert = True
                _mo_v, _dv = _days_vacant(unit_num, mo_date)
                detailed_lists["vacantNotReadyList"].append({
                    "unit": unit_num,
                    "unitId": unit_id,
                    "type": unit_type,
                    "avDate": av_date,
                    "lastMoDate": _mo_v,
                    "daysVacant": _dv,
                    "hasAlert": vnr_alert,
                    "alertMsg": vnr_alert_msg
                })
        elif vacancy_status == "Vacant Pre-Leased":
            # Leased vacant - add to appropriate ready/not ready AND track as leased
            if is_ready:
                status_summary["Vacant Ready"].append(unit_num)
                _mo_v, _dv = _days_vacant(unit_num, mo_date)
                detailed_lists["vacantReadyList"].append({
                    "unit": unit_num,
                    "unitId": unit_id,
                    "type": unit_type,
                    "avDate": av_date,
                    "lastMoDate": _mo_v,
                    "daysVacant": _dv
                })
            else:
                status_summary["Vacant Not Ready"].append(unit_num)
                # Check for alert: available date in the past
                vnr_alert = False
                vnr_alert_msg = ""
                if av_date:
                    try:
                        av_dt = datetime.strptime(av_date, "%Y-%m-%d").date()
                        if av_dt < today:
                            vnr_alert = True
                            vnr_alert_msg = "Past Available Date"
                    except: pass
                if unit_type and 'classic' in unit_type.lower():
                    vnr_alert_msg = (vnr_alert_msg + " • " if vnr_alert else "") + "Classic Unit - please update the unit type"
                    vnr_alert = True
                _mo_v, _dv = _days_vacant(unit_num, mo_date)
                detailed_lists["vacantNotReadyList"].append({
                    "unit": unit_num,
                    "unitId": unit_id,
                    "type": unit_type,
                    "avDate": av_date,
                    "lastMoDate": _mo_v,
                    "daysVacant": _dv,
                    "hasAlert": vnr_alert,
                    "alertMsg": vnr_alert_msg
                })
            status_summary["Vacant Leased"].append(unit_num)  # Also track as leased
        elif vacancy_status == "Notice to Vacate":
            # Check if resident already moved out (lease is "Former") - treat as Vacant
            occ_lease_id = occ_lease.get("leaseId", "") if occ_lease else ""
            occ_lease_status = lease_status_by_id.get(occ_lease_id, "")
            is_former = occ_lease_status == "Former"

            if is_former:
                # Resident moved out - treat as Vacant
                if is_ready:
                    status_summary["Vacant Ready"].append(unit_num)
                    _mo_v, _dv = _days_vacant(unit_num, mo_date)
                    detailed_lists["vacantReadyList"].append({
                        "unit": unit_num,
                        "unitId": unit_id,
                        "type": unit_type,
                        "avDate": av_date,
                        "lastMoDate": _mo_v,
                        "daysVacant": _dv
                    })
                else:
                    status_summary["Vacant Not Ready"].append(unit_num)
                    # Check for alert: available date in the past
                    vnr_alert = False
                    vnr_alert_msg = ""
                    if av_date:
                        try:
                            av_dt = datetime.strptime(av_date, "%Y-%m-%d").date()
                            if av_dt < today:
                                vnr_alert = True
                                vnr_alert_msg = "Past Available Date"
                        except: pass
                    if unit_type and 'classic' in unit_type.lower():
                        vnr_alert_msg = (vnr_alert_msg + " • " if vnr_alert else "") + "Classic Unit - please update the unit type"
                        vnr_alert = True
                    _mo_v, _dv = _days_vacant(unit_num, mo_date)
                    detailed_lists["vacantNotReadyList"].append({
                        "unit": unit_num,
                        "unitId": unit_id,
                        "type": unit_type,
                        "avDate": av_date,
                        "lastMoDate": _mo_v,
                        "daysVacant": _dv,
                        "hasAlert": vnr_alert,
                        "alertMsg": vnr_alert_msg
                    })
            else:
                status_summary["NTV"].append(unit_num)
                ntv_alert, ntv_alert_msg, auto_pushed = _ntv_alert_and_push(
                    property_id, unit_id, unit_num, unit_type, mo_date, av_date, today
                )
                detailed_lists["ntvList"].append({
                    "unit": unit_num,
                    "unitId": unit_id,
                    "type": unit_type,
                    "ntvDate": ntv_date,
                    "moDate": mo_date,
                    "avDate": av_date,
                    "hasAlert": ntv_alert,
                    "alertMsg": ntv_alert_msg,
                    "autoPushed": auto_pushed,
                })
        elif vacancy_status == "Notice to Vacate Pre-Leased":
            # Check if resident already moved out (lease is "Former") - treat as Vacant
            occ_lease_id = occ_lease.get("leaseId", "") if occ_lease else ""
            occ_lease_status = lease_status_by_id.get(occ_lease_id, "")
            is_former = occ_lease_status == "Former"

            if is_former:
                # Resident moved out - treat as Vacant (leased)
                if is_ready:
                    status_summary["Vacant Ready"].append(unit_num)
                    _mo_v, _dv = _days_vacant(unit_num, mo_date)
                    detailed_lists["vacantReadyList"].append({
                        "unit": unit_num,
                        "unitId": unit_id,
                        "type": unit_type,
                        "avDate": av_date,
                        "lastMoDate": _mo_v,
                        "daysVacant": _dv
                    })
                else:
                    status_summary["Vacant Not Ready"].append(unit_num)
                    # Check for alert: available date in the past
                    vnr_alert = False
                    vnr_alert_msg = ""
                    if av_date:
                        try:
                            av_dt = datetime.strptime(av_date, "%Y-%m-%d").date()
                            if av_dt < today:
                                vnr_alert = True
                                vnr_alert_msg = "Past Available Date"
                        except: pass
                    if unit_type and 'classic' in unit_type.lower():
                        vnr_alert_msg = (vnr_alert_msg + " • " if vnr_alert else "") + "Classic Unit - please update the unit type"
                        vnr_alert = True
                    _mo_v, _dv = _days_vacant(unit_num, mo_date)
                    detailed_lists["vacantNotReadyList"].append({
                        "unit": unit_num,
                        "unitId": unit_id,
                        "type": unit_type,
                        "avDate": av_date,
                        "lastMoDate": _mo_v,
                        "daysVacant": _dv,
                        "hasAlert": vnr_alert,
                        "alertMsg": vnr_alert_msg
                    })
                status_summary["Vacant Leased"].append(unit_num)
            else:
                status_summary["NTV"].append(unit_num)  # Count in NTV total
                status_summary["NTV Leased"].append(unit_num)  # Also track as leased
                ntv_alert, ntv_alert_msg, auto_pushed = _ntv_alert_and_push(
                    property_id, unit_id, unit_num, unit_type, mo_date, av_date, today
                )
                detailed_lists["ntvList"].append({
                    "unit": unit_num,
                    "unitId": unit_id,
                    "type": unit_type,
                    "ntvDate": ntv_date,
                    "moDate": mo_date,
                    "avDate": av_date,
                    "hasAlert": ntv_alert,
                    "alertMsg": ntv_alert_msg,
                    "autoPushed": auto_pushed,
                })
        else:
            status_summary["Other"].append(unit_num)

    # Total units excludes Down/Abated (they were skipped in the loop above)
    # Only count main categories, not subsets (Vacant Leased, NTV Leased are subsets)
    total_units = (
        len(status_summary.get("Occupied", [])) +
        len(status_summary.get("Vacant Ready", [])) +
        len(status_summary.get("Vacant Not Ready", [])) +
        len(status_summary.get("NTV", [])) +
        len(status_summary.get("Under Eviction", [])) +
        len(status_summary.get("Other", []))
    )
    # Physical occupancy: Total - all vacant (ready + not ready, which includes leased)
    vacant_count = len(status_summary.get("Vacant Ready", [])) + len(status_summary.get("Vacant Not Ready", []))
    occupancy = round((total_units - vacant_count) / total_units * 100, 2) if total_units else 0

    return {
        "total_units": total_units,
        "status_breakdown": {k: len(v) for k, v in status_summary.items()},
        "unit_lists": {k: sorted(v) for k, v in status_summary.items()},
        "occupancy_rate": occupancy,
        "eviction_units": list(eviction_units),  # Convert set to list for JSON serialization
        "units_availability": units,  # Raw units data for off-market/renovation calculations
        # Detailed lists for pop-ups
        "vacantReadyList": sorted(detailed_lists["vacantReadyList"], key=lambda x: x["unit"]),
        "vacantNotReadyList": sorted(detailed_lists["vacantNotReadyList"], key=lambda x: x["unit"]),
        "ntvList": sorted(detailed_lists["ntvList"], key=lambda x: x["unit"]),
        "evictionList": sorted(detailed_lists["evictionList"], key=lambda x: x["unit"]),
        "offMarketList": sorted(detailed_lists["offMarketList"], key=lambda x: x["unit"])
    }

def generate_html(properties_data, traffic_data, leases_data, financial_data,
                  prev_alert_state=None, alert_today=None):
    """Generate HTML dashboard. Returns (html_str, new_alert_state)."""
    from alert_state import apply_state_to_properties
    # Use Florida time (Eastern Time)
    from zoneinfo import ZoneInfo
    florida_tz = ZoneInfo("America/New_York")
    today = datetime.now(florida_tz)
    today_str = today.strftime("%B %d, %Y at %I:%M %p ET")
    month_str = today.strftime("%B %Y")
    timestamp = today.strftime("%B %d, %Y at %I:%M %p ET")
    time_only = today.strftime("%I:%M %p ET")

    # Build property options (add "All Properties" first)
    property_options = '<option value="ALL">All Properties (Portfolio Total)</option>\n'
    property_options += "\n".join([
        f'<option value="{abbr}">{abbr} - {data["name"]}</option>'
        for abbr, data in properties_data.items()
    ])

    # Calculate portfolio totals
    portfolio_totals = {
        "name": "All Properties",
        "total_units": 0,
        "status_breakdown": defaultdict(int),
        "unit_lists": defaultdict(list)
    }

    for abbr, data in properties_data.items():
        portfolio_totals["total_units"] += data["total_units"]
        for status, count in data["status_breakdown"].items():
            portfolio_totals["status_breakdown"][status] += count
        for status, units in data["unit_lists"].items():
            portfolio_totals["unit_lists"][status].extend([f"{abbr}:{u}" for u in units])

    portfolio_totals["status_breakdown"] = dict(portfolio_totals["status_breakdown"])
    portfolio_totals["unit_lists"] = {k: sorted(v) for k, v in portfolio_totals["unit_lists"].items()}
    # Occupancy: only Vacant and Vacant Leased count as unoccupied
    vacant_total = portfolio_totals["status_breakdown"].get("Vacant", 0) + portfolio_totals["status_breakdown"].get("Vacant Leased", 0)
    portfolio_totals["occupancy_rate"] = round((portfolio_totals["total_units"] - vacant_total) / portfolio_totals["total_units"] * 100, 2) if portfolio_totals["total_units"] else 0

    # Add portfolio to data
    all_data = {"ALL": portfolio_totals}
    all_data.update(properties_data)

    # Build JS data
    js_data = json.dumps(all_data, indent=2)
    financial_data_js = json.dumps(financial_data, indent=2)

    # Build property summary table data with region grouping
    table_data = {}
    for abbr, data in properties_data.items():
        sb = data["status_breakdown"]
        # Get future moves data from leases_data
        future_moves = leases_data.get(abbr, {}).get("future_moves", {})
        lease_info = leases_data.get(abbr, {})

        # Build Vacant Leased and NTV Leased lists from applicants data
        # Match pending leases to pre-leased unit numbers
        applicants_list = lease_info.get("applicantsList", [])
        vacant_leased_units = set(data.get("unit_lists", {}).get("Vacant Leased", []))
        ntv_leased_units = set(data.get("unit_lists", {}).get("NTV Leased", []))

        # Get today for alert calculations
        today = datetime.now().date()

        vacant_leased_list = []
        ntv_leased_list = []
        for app in applicants_list:
            unit = app.get("unit", "")
            move_in = app.get("moveIn", "")

            # Check for alert: move-in date in the past
            vl_alert = False
            vl_alert_msg = ""
            if move_in:
                try:
                    mi_dt = datetime.strptime(move_in[:10], "%Y-%m-%d").date()
                    if mi_dt < today:
                        vl_alert = True
                        vl_alert_msg = "Past Move-In Date"
                except: pass

            if unit in vacant_leased_units:
                vacant_leased_list.append({
                    "unit": unit,
                    "unitId": app.get("unitId", ""),
                    "type": app.get("type", ""),
                    "applicant": app.get("applicant", ""),
                    "leaseId": app.get("leaseId", ""),
                    "moveIn": move_in,
                    "hasAlert": vl_alert,
                    "alertMsg": vl_alert_msg,
                    "isTransfer": app.get("isTransfer", False)
                })
            if unit in ntv_leased_units:
                ntv_leased_list.append({
                    "unit": unit,
                    "unitId": app.get("unitId", ""),
                    "type": app.get("type", ""),
                    "applicant": app.get("applicant", ""),
                    "leaseId": app.get("leaseId", ""),
                    "moveIn": move_in,
                    "hasAlert": vl_alert,
                    "alertMsg": vl_alert_msg,
                    "isTransfer": app.get("isTransfer", False)
                })

        # Calculate Future MI from Vacant Leased + NTV Leased with move-in in current month
        # Get current month boundaries
        current_year = today.year
        current_month = today.month
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        future_moves_month = f"{month_names[current_month - 1]} {current_year}"

        # Filter to move-ins in current month (any date in the month, not just today forward)
        future_mi_list = []
        for item in vacant_leased_list + ntv_leased_list:
            move_in_str = item.get("moveIn", "")
            if move_in_str and len(move_in_str) >= 10:
                try:
                    move_in_date = datetime.strptime(move_in_str[:10], "%Y-%m-%d").date()
                    if move_in_date.year == current_year and move_in_date.month == current_month:
                        future_mi_list.append({
                            "unit": item.get("unit", ""),
                            "unitId": item.get("unitId", ""),
                            "type": item.get("type", ""),
                            "resident": item.get("applicant", ""),
                            "leaseId": item.get("leaseId", ""),
                            "signed": "",  # Not tracking signed date for this logic
                            "moveIn": move_in_str[:10],
                            "isTransfer": item.get("isTransfer", False)
                        })
                except:
                    pass

        future_mi_count = len(future_mi_list)
        future_mi_list_sorted = sorted(future_mi_list, key=lambda x: x["unit"])

        # Calculate alert counts
        vnr_alerts = sum(1 for x in data.get("vacantNotReadyList", []) if x.get("hasAlert"))
        ntv_alerts = sum(1 for x in data.get("ntvList", []) if x.get("hasAlert"))
        vl_alerts = sum(1 for x in vacant_leased_list if x.get("hasAlert"))

        table_data[abbr] = {
            "abbr": abbr,
            "name": data["name"],
            "total": data["total_units"],
            "occupied": sb.get("Occupied", 0),
            "vacantReady": sb.get("Vacant Ready", 0),  # Includes leased that are ready
            "vacantNotReady": sb.get("Vacant Not Ready", 0),  # Includes leased that are not ready
            "vacantLeased": len(vacant_leased_list),  # Subset: matches popup list
            "ntv": sb.get("NTV", 0),  # Includes NTV Leased
            "ntvLeased": len(ntv_leased_list),  # Subset: matches popup list
            "eviction": sb.get("Under Eviction", 0),
            "offMarket": data.get("off_market", 0),  # Units not available for online marketing
            "occupancy": data["occupancy_rate"],
            "futureMI": future_mi_count,  # From Vacant Leased + NTV Leased with move-in in current month
            "futureMO": future_moves.get("future_mo", 0),
            "futureMovesMonth": future_moves_month,
            # Alert counts
            "vacantNotReadyAlerts": vnr_alerts,
            "ntvAlerts": ntv_alerts,
            "vacantLeasedAlerts": vl_alerts,
            # Detailed lists for pop-ups
            "vacantReadyList": data.get("vacantReadyList", []),
            "vacantNotReadyList": data.get("vacantNotReadyList", []),
            "ntvList": data.get("ntvList", []),
            "evictionList": data.get("evictionList", []),
            "offMarketList": data.get("offMarketList", []),
            "vacantLeasedList": vacant_leased_list,
            "ntvLeasedList": ntv_leased_list,
            "futureMIList": future_mi_list_sorted,
            "futureMOList": future_moves.get("futureMOList", []),
            "insufficientNoticeList": lease_info.get("expiring", {}).get("insufficientNoticeList", []),
            "pendingInRealUnitList": lease_info.get("pendingInRealUnitList", []),
            "approvedPaidInHoldingList": lease_info.get("approvedPaidInHoldingList", []),
        }

    # Age alerts: mutate table_data entries to carry firstSeen + daysAgo; return
    # new state dict so main() can persist it.
    _alert_today = alert_today or datetime.now().date()
    new_alert_state = apply_state_to_properties(table_data, prev_alert_state or {}, _alert_today)

    table_data_js = json.dumps(table_data)
    property_groups_js = json.dumps(PROPERTY_GROUPS)
    traffic_data_js = json.dumps(traffic_data)
    leases_data_js = json.dumps(leases_data)
    month_str_js = json.dumps(month_str)

    html = f'''<!DOCTYPE html>
<html lang="en" class="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SNS Daily KPIs</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
        }}
    </script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        /* Tone pure white down to off-white in LIGHT MODE ONLY. */
        html:not(.dark) .bg-white {{ background-color: #fafbfc !important; }}

        /* Custom Select Dropdown */
        select {{
            -webkit-appearance: none;
            -moz-appearance: none;
            appearance: none;
            background-image: none;
        }}
        select::-ms-expand {{
            display: none;
        }}
        select option {{
            padding: 12px;
            background: #fafbfc;
            color: #1e293b;
        }}
        .dark select option {{
            background: #334155;
            color: white;
        }}
        .fancy-scroll {{
            scrollbar-width: thin;
            scrollbar-color: #94a3b8 #e2e8f0;
        }}
        .dark .fancy-scroll {{
            scrollbar-color: #64748b #1e293b;
        }}
        .fancy-scroll::-webkit-scrollbar {{ width: 8px; height: 8px; }}
        .fancy-scroll::-webkit-scrollbar-track {{ background: #e2e8f0; border-radius: 10px; }}
        .dark .fancy-scroll::-webkit-scrollbar-track {{ background: #1e293b; }}
        .fancy-scroll::-webkit-scrollbar-thumb {{
            background: linear-gradient(180deg, #94a3b8 0%, #64748b 100%);
            border-radius: 10px;
        }}
        .dark .fancy-scroll::-webkit-scrollbar-thumb {{
            background: linear-gradient(180deg, #64748b 0%, #475569 100%);
        }}
        .kpi-card {{ transition: transform 0.2s ease, box-shadow 0.2s ease; }}
        .kpi-card:hover {{ transform: translateY(-2px); box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1); }}
        .progress-bar {{ transition: width 0.5s ease-in-out; }}
        .table-row:hover {{ background-color: rgba(59, 130, 246, 0.05); }}
        .dark .table-row:hover {{ background-color: rgba(59, 130, 246, 0.1); }}

        /* Custom tooltips */
        .tip {{
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 14px;
            height: 14px;
            margin-left: 4px;
            font-size: 9px;
            font-weight: 600;
            background: #e2e8f0;
            color: #64748b;
            border-radius: 50%;
            cursor: help;
        }}
        .dark .tip {{
            background: #475569;
            color: #94a3b8;
        }}
        .tip:hover::after {{
            content: attr(data-tip);
            position: absolute;
            left: calc(100% + 6px);
            top: 50%;
            transform: translateY(-50%);
            padding: 6px 10px;
            background: #fafbfc;
            color: #334155;
            font-size: 11px;
            font-weight: 500;
            white-space: nowrap;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            border: 1px solid #e2e8f0;
            z-index: 100;
        }}
        .dark .tip:hover::after {{
            background: #1e293b;
            color: #e2e8f0;
            border-color: #475569;
        }}

        /* Financial cell tooltips (top-center) */
        .fin-cell {{
            position: relative;
            cursor: default;
        }}
        .fin-cell[data-count]:hover::after {{
            content: attr(data-count);
            position: absolute;
            bottom: calc(100% + 4px);
            left: 50%;
            transform: translateX(-50%);
            padding: 4px 8px;
            background: #fafbfc;
            color: #334155;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
            border-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            border: 1px solid #e2e8f0;
            z-index: 100;
        }}
        .dark .fin-cell[data-count]:hover::after {{
            background: #1e293b;
            color: #e2e8f0;
            border-color: #475569;
        }}

        /* Alert badges */
        .alert-dot {{
            position: absolute;
            top: 4px;
            right: 4px;
            width: 5px;
            height: 5px;
            background-color: #dc2626;
            border-radius: 50%;
        }}
        .dark .alert-dot {{
            background-color: #f87171;
        }}
        /* Transfer indicator (yellow dot) */
        .transfer-dot {{
            position: absolute;
            top: 4px;
            right: 4px;
            width: 5px;
            height: 5px;
            background-color: #eab308;
            border-radius: 50%;
        }}
        .dark .transfer-dot {{
            background-color: #facc15;
        }}
        /* When both alert and transfer, show alert (red takes priority) */
        .alert-dot.transfer-dot {{
            background-color: #dc2626;
        }}
        .dark .alert-dot.transfer-dot {{
            background-color: #f87171;
        }}
        .alert-tooltip {{
            cursor: help;
        }}
        .alert-tooltip::after {{
            content: attr(data-tooltip);
            position: absolute;
            right: calc(100% + 6px);
            left: auto;
            top: 50%;
            transform: translateY(-50%);
            padding: 6px 10px;
            background: #fafbfc;
            color: #334155;
            font-size: 11px;
            font-weight: 500;
            white-space: nowrap;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            border: 1px solid #e2e8f0;
            z-index: 100;
            opacity: 0;
            visibility: hidden;
            transition: opacity 0.2s;
        }}
        .dark .alert-tooltip::after {{
            background: #1e293b;
            color: #e2e8f0;
            border-color: #475569;
        }}
        .alert-tooltip:hover::after {{
            opacity: 1;
            visibility: visible;
        }}
        .alert-tooltip.alert-tooltip-right::after {{
            right: auto;
            left: calc(100% + 6px);
        }}
        input[type="checkbox"] {{
            accent-color: #1e40af;
        }}

        /* Custom Scrollbar Styles */
        * {{
            scrollbar-width: thin;
            scrollbar-color: #94a3b8 transparent;
        }}
        .dark * {{
            scrollbar-color: #475569 transparent;
        }}
        *::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        *::-webkit-scrollbar-track {{
            background: transparent;
            border-radius: 10px;
        }}
        *::-webkit-scrollbar-thumb {{
            background: linear-gradient(180deg, #94a3b8 0%, #64748b 100%);
            border-radius: 10px;
            border: 2px solid transparent;
            background-clip: padding-box;
        }}
        *::-webkit-scrollbar-thumb:hover {{
            background: linear-gradient(180deg, #64748b 0%, #475569 100%);
            background-clip: padding-box;
        }}
        .dark *::-webkit-scrollbar-thumb {{
            background: linear-gradient(180deg, #475569 0%, #334155 100%);
            background-clip: padding-box;
        }}
        .dark *::-webkit-scrollbar-thumb:hover {{
            background: linear-gradient(180deg, #64748b 0%, #475569 100%);
            background-clip: padding-box;
        }}
        *::-webkit-scrollbar-corner {{
            background: transparent;
        }}

        /* Mobile Responsive Styles */
        @media (max-width: 768px) {{
            body {{ padding: 12px !important; }}

            /* Header - stack vertically */
            .mobile-header {{
                flex-direction: column !important;
                gap: 12px !important;
                text-align: center;
            }}
            .mobile-header > div:first-child {{
                justify-content: center;
            }}
            .mobile-controls {{
                flex-wrap: nowrap !important;
                justify-content: center !important;
                gap: 6px !important;
            }}
            /* Hide labels on mobile */
            .mobile-controls label {{
                display: none !important;
            }}
            /* Compact timestamp on mobile */
            .mobile-controls .h-11 {{
                height: auto !important;
                padding: 8px 10px !important;
            }}
            .mobile-controls #refresh-timestamp {{
                font-size: 11px !important;
            }}

            /* KPI Grid - 2 columns */
            #kpi-section {{
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 8px !important;
            }}

            /* Tables - tighter padding */
            table th, table td {{
                padding: 4px 6px !important;
                font-size: 10px !important;
            }}

            /* Table scroll indicator */
            .table-scroll-container {{
                position: relative;
            }}
            .table-scroll-container::after {{
                content: '';
                position: absolute;
                right: 0;
                top: 0;
                bottom: 0;
                width: 20px;
                background: linear-gradient(90deg, transparent, rgba(255,255,255,0.8));
                pointer-events: none;
            }}
            .dark .table-scroll-container::after {{
                background: linear-gradient(90deg, transparent, rgba(30,41,59,0.8));
            }}

            /* Charts - stack vertically */
            #charts-section {{
                grid-template-columns: 1fr !important;
            }}

            /* Unit lists - stack vertically */
            #unit-lists-section {{
                grid-template-columns: 1fr !important;
            }}

            /* Reduce gaps and padding */
            .max-w-7xl.space-y-5 > * + * {{
                margin-top: 12px !important;
            }}
            .p-5 {{ padding: 12px !important; }}
            .p-4 {{ padding: 10px !important; }}
            .gap-5 {{ gap: 12px !important; }}
            .gap-3 {{ gap: 8px !important; }}

            /* Section titles smaller */
            .text-lg {{ font-size: 14px !important; }}
            .text-2xl {{ font-size: 18px !important; }}
        }}
    </style>
</head>
<body class="bg-slate-100 dark:bg-slate-900 p-6 font-sans text-slate-800 dark:text-slate-100 transition-colors duration-200">
    <!-- Loading Overlay -->
    <div id="refresh-overlay" class="fixed inset-0 bg-slate-900/70 backdrop-blur-sm z-50 hidden items-center justify-center">
        <div class="bg-white dark:bg-slate-800 rounded-2xl p-8 shadow-2xl max-w-sm mx-4 text-center">
            <div class="relative w-16 h-16 mx-auto mb-4">
                <div class="absolute inset-0 rounded-full border-4 border-slate-200 dark:border-slate-700"></div>
                <div class="absolute inset-0 rounded-full border-4 border-blue-500 border-t-transparent animate-spin"></div>
            </div>
            <h3 class="text-lg font-semibold text-slate-900 dark:text-white mb-2">Refreshing Dashboard</h3>
            <p id="refresh-status" class="text-sm text-slate-500 dark:text-slate-400">Triggering data refresh...</p>
            <div class="mt-4 bg-slate-100 dark:bg-slate-700 rounded-full h-2 overflow-hidden">
                <div id="refresh-progress" class="bg-blue-500 h-full transition-all duration-500" style="width: 5%"></div>
            </div>
            <p id="refresh-time" class="text-xs text-slate-400 dark:text-slate-500 mt-2">Estimated: ~3 minutes</p>
        </div>
    </div>

    <div class="max-w-7xl mx-auto space-y-5">

        <!-- Header -->
        <div class="mobile-header flex justify-between items-center bg-white dark:bg-slate-800 p-5 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700">
            <div class="flex items-center gap-3">
                <div class="w-14 h-14 flex items-center justify-center">
                    <img src="https://www.dropbox.com/scl/fi/ss6tqrjk1xyldcwl5g3w9/SNS-Blue.png?rlkey=zcsommki78v65qmk6zvzo0jxh&st=ie5tvrph&raw=1" alt="SNS Logo" class="w-14 h-14 object-contain dark:hidden">
                    <img src="https://www.dropbox.com/scl/fi/8qha3kw2j503jbd037ahh/SNS-White.png?rlkey=s3gjbikiohpe00vpcx8x1td3r&st=efmsepz3&raw=1" alt="SNS Logo" class="w-14 h-14 object-contain hidden dark:block">
                </div>
                <div>
                    <h1 class="text-2xl font-bold text-slate-900 dark:text-white">SNS Daily KPIs</h1>
                </div>
            </div>
            <div class="mobile-controls flex items-center space-x-3">
                <!-- Property Dropdown - Hidden until individual property pages are ready -->
                <div class="relative hidden">
                    <label class="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Property</label>
                    <div class="relative">
                        <select id="propertyFilter" onchange="updateDashboard()" class="block w-64 h-11 rounded-xl border-2 border-slate-200 dark:border-slate-600 py-2.5 pl-4 pr-10 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-medium cursor-pointer text-sm appearance-none focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400 transition-all">
                            {property_options}
                        </select>
                        <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3">
                            <svg class="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        </div>
                    </div>
                </div>
                <!-- Date Display -->
                <div>
                    <label class="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">&nbsp;</label>
                    <div class="h-11 py-2.5 px-4 bg-slate-100 dark:bg-slate-700 rounded-xl border-2 border-slate-200 dark:border-slate-600 flex items-center">
                        <span id="refresh-timestamp" class="font-semibold text-slate-700 dark:text-slate-200 text-sm">{today_str}</span>
                    </div>
                </div>
                <!-- Refresh Button -->
                <div>
                    <label class="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">&nbsp;</label>
                    <button id="refresh-btn" onclick="refreshData()" class="h-11 w-11 rounded-xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-all outline-none focus:ring-2 focus:ring-blue-400 border-2 border-slate-200 dark:border-slate-600 flex items-center justify-center" title="Refresh Data (~3 min)">
                        <svg id="refresh-icon" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                        <svg id="refresh-spinner" class="w-5 h-5 hidden animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    </button>
                </div>
                <!-- Alerts Bell (ops) -->
                <div>
                    <label class="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">&nbsp;</label>
                    <button id="alerts-btn" onclick="opsAlerts.show()" class="relative h-11 w-11 rounded-xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-all outline-none focus:ring-2 focus:ring-blue-400 border-2 border-slate-200 dark:border-slate-600 flex items-center justify-center" title="Operational Alerts">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path></svg>
                        <span id="alerts-badge" class="hidden absolute -top-1 -right-1 bg-red-500 text-white text-[10px] font-bold rounded-full min-w-[18px] h-[18px] px-1 flex items-center justify-center border-2 border-white dark:border-slate-800">0</span>
                    </button>
                </div>
                <!-- Leasing Alerts -->
                <div>
                    <label class="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">&nbsp;</label>
                    <button id="leasing-btn" onclick="leasingAlerts.show()" class="relative h-11 w-11 rounded-xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-all outline-none focus:ring-2 focus:ring-blue-400 border-2 border-slate-200 dark:border-slate-600 flex items-center justify-center" title="Leasing Alerts">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                        <span id="leasing-badge" class="hidden absolute -top-1 -right-1 bg-amber-500 text-white text-[10px] font-bold rounded-full min-w-[18px] h-[18px] px-1 flex items-center justify-center border-2 border-white dark:border-slate-800">0</span>
                    </button>
                </div>
                <!-- Theme Toggle -->
                <div>
                    <label class="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">&nbsp;</label>
                    <button id="theme-toggle" class="h-11 w-11 rounded-xl bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-all outline-none focus:ring-2 focus:ring-blue-400 border-2 border-slate-200 dark:border-slate-600 flex items-center justify-center">
                        <svg id="theme-toggle-dark-icon" class="w-5 h-5 hidden" fill="currentColor" viewBox="0 0 20 20"><path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z"></path></svg>
                        <svg id="theme-toggle-light-icon" class="w-5 h-5 hidden" fill="currentColor" viewBox="0 0 20 20"><path d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" fill-rule="evenodd" clip-rule="evenodd"></path></svg>
                    </button>
                </div>
            </div>
        </div>

        <!-- KPI Cards -->
        <div class="grid grid-cols-7 gap-3" id="kpi-section"></div>

        <!-- Property Summary Table (shown only for ALL) - Transposed view -->
        <div id="portfolio-table-section" class="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div class="p-4 border-b border-slate-200 dark:border-slate-700">
                <h2 class="text-lg font-bold text-slate-800 dark:text-white">Portfolio Summary</h2>
            </div>
            <div class="overflow-x-auto fancy-scroll">
                <table class="min-w-full text-xs table-fixed" id="portfolio-matrix-table">
                    <!-- Generated by JS -->
                </table>
            </div>
        </div>

        <!-- Traffic (MTD) Table (shown only for ALL) -->
        <div id="traffic-mtd-section" class="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div class="p-4 border-b border-slate-200 dark:border-slate-700">
                <h2 class="text-lg font-bold text-slate-800 dark:text-white">Traffic Month-to-Date <span class="text-sm font-normal text-slate-500 dark:text-slate-400" id="traffic-month"></span></h2>
            </div>
            <div class="overflow-x-auto fancy-scroll">
                <table class="min-w-full text-xs table-fixed" id="traffic-mtd-matrix-table">
                    <!-- Generated by JS -->
                </table>
            </div>
        </div>

        <!-- Traffic (Snapshot) Table (shown only for ALL) -->
        <div id="traffic-snapshot-section" class="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div class="p-4 border-b border-slate-200 dark:border-slate-700">
                <h2 class="text-lg font-bold text-slate-800 dark:text-white">Traffic Snapshot</h2>
            </div>
            <div class="overflow-x-auto fancy-scroll">
                <table class="min-w-full text-xs table-fixed" id="traffic-snapshot-matrix-table">
                    <!-- Generated by JS -->
                </table>
            </div>
        </div>

        <!-- Expiring Leases Table (shown only for ALL) -->
        <div id="expiring-table-section" class="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div class="p-4 border-b border-slate-200 dark:border-slate-700">
                <h2 class="text-lg font-bold text-slate-800 dark:text-white">Expiring Leases <span class="text-sm font-normal text-slate-500 dark:text-slate-400">(Next 3 Months)</span></h2>
            </div>
            <div class="overflow-x-auto fancy-scroll">
                <table class="min-w-full text-xs table-fixed" id="expiring-matrix-table">
                    <!-- Generated by JS -->
                </table>
            </div>
        </div>

        <!-- Renovation & Maintenance Table (shown only for ALL) -->
        <div id="renovation-table-section" class="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div class="p-4 border-b border-slate-200 dark:border-slate-700">
                <h2 class="text-lg font-bold text-slate-800 dark:text-white">Renovation & Maintenance</h2>
            </div>
            <!-- Renovation Table -->
            <div class="overflow-x-auto fancy-scroll">
                <table class="min-w-full text-xs table-fixed" id="renovation-matrix-table">
                    <!-- Generated by JS -->
                </table>
            </div>
        </div>

        <!-- Financial Summary Table (shown only for ALL) -->
        <div id="financial-table-section" class="bg-white dark:bg-slate-800 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div class="p-4 border-b border-slate-200 dark:border-slate-700">
                <h2 class="text-lg font-bold text-slate-800 dark:text-white">Financial Summary</h2>
            </div>
            <!-- Financial Table -->
            <div class="overflow-x-auto fancy-scroll">
                <table class="min-w-full text-xs table-fixed" id="financial-matrix-table">
                    <!-- Generated by JS -->
                </table>
            </div>
        </div>

        <!-- Charts Row -->
        <div id="charts-section" class="grid grid-cols-2 gap-5">
            <div class="bg-white dark:bg-slate-800 p-5 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700">
                <h2 class="text-base font-bold text-slate-800 dark:text-white mb-3">Unit Status Distribution</h2>
                <div class="relative h-56 flex items-center justify-center">
                    <canvas id="statusChart"></canvas>
                </div>
            </div>
            <div class="bg-white dark:bg-slate-800 p-5 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-700">
                <h2 class="text-base font-bold text-slate-800 dark:text-white mb-3">Status Breakdown</h2>
                <div class="space-y-3" id="progress-bars"></div>
            </div>
        </div>

        <!-- Unit Lists -->
        <div class="grid grid-cols-3 gap-5" id="unit-lists-section"></div>

        <!-- Footer -->
        <div class="bg-slate-100 dark:bg-slate-800/50 p-3 rounded-xl border border-slate-200 dark:border-slate-700">
            <div class="flex items-center justify-center text-xs">
                <span class="text-slate-500 dark:text-slate-400">Data Source: <span class="font-medium text-slate-700 dark:text-slate-300">ResMan API</span></span>
            </div>
        </div>
    </div>

    <!-- Pop-up Modal -->
    <div id="popup-modal" class="hidden fixed inset-0 z-50 overflow-y-auto" onclick="if(event.target===this)closePopup()">
        <div class="flex items-center justify-center min-h-screen p-4">
            <div class="fixed inset-0 bg-black/50 dark:bg-black/70"></div>
            <div class="relative bg-white dark:bg-slate-800 rounded-2xl shadow-xl w-[min(1100px,92vw)] h-[85vh] overflow-hidden flex flex-col">
                <div class="flex items-center justify-between p-4 border-b border-slate-200 dark:border-slate-700 flex-shrink-0">
                    <h3 id="popup-title" class="text-lg font-bold text-slate-800 dark:text-white"></h3>
                    <button onclick="closePopup()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 text-2xl leading-none">&times;</button>
                </div>
                <div id="popup-filters" class="hidden px-4 py-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-700/30 flex-shrink-0">
                    <div class="flex flex-wrap gap-3 items-center">
                        <label class="text-sm font-medium text-slate-600 dark:text-slate-300">Status:</label>
                        <div id="popup-status-filter" class="relative"></div>
                    </div>
                </div>
                <div class="p-4 overflow-auto flex-1 min-h-0">
                    <table id="popup-table" class="min-w-full text-sm">
                        <thead id="popup-thead" class="bg-slate-50 dark:bg-slate-700/50"></thead>
                        <tbody id="popup-tbody" class="divide-y divide-slate-100 dark:divide-slate-700"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- Operational Alerts Modal -->
    <div id="alerts-modal" class="hidden fixed inset-0 z-50 overflow-y-auto" onclick="if(event.target===this)opsAlerts.close()">
        <div class="flex items-center justify-center min-h-screen p-4">
            <div class="fixed inset-0 bg-black/50 dark:bg-black/70"></div>
            <div class="relative bg-white dark:bg-slate-800 rounded-2xl shadow-xl w-[min(1300px,95vw)] h-[85vh] overflow-hidden flex flex-col">
                <div class="flex items-center justify-between p-4 border-b border-slate-200 dark:border-slate-700 flex-shrink-0">
                    <h3 class="text-lg font-bold text-slate-800 dark:text-white flex items-center gap-2">
                        <svg class="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path></svg>
                        Operational Alerts <span id="alerts-modal-count" class="text-sm font-semibold text-white bg-red-500 rounded-full px-2 py-0.5">0</span>
                    </h3>
                    <button onclick="opsAlerts.close()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 text-2xl leading-none">&times;</button>
                </div>
                <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-700/30 flex-shrink-0">
                    <div class="flex flex-wrap gap-4 items-start">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Properties</label>
                            <div id="alerts-property-filter" class="relative"></div>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Category</label>
                            <div id="alerts-category-filter" class="relative"></div>
                        </div>
                    </div>
                </div>
                <div class="p-4 overflow-auto flex-1 min-h-0">
                    <table class="min-w-full text-sm">
                        <thead id="alerts-thead" class="bg-slate-50 dark:bg-slate-700/50"></thead>
                        <tbody id="alerts-tbody" class="divide-y divide-slate-100 dark:divide-slate-700"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- Leasing Alerts Modal -->
    <div id="leasing-modal" class="hidden fixed inset-0 z-50 overflow-y-auto" onclick="if(event.target===this)leasingAlerts.close()">
        <div class="flex items-center justify-center min-h-screen p-4">
            <div class="fixed inset-0 bg-black/50 dark:bg-black/70"></div>
            <div class="relative bg-white dark:bg-slate-800 rounded-2xl shadow-xl w-[min(1300px,95vw)] h-[85vh] overflow-hidden flex flex-col">
                <div class="flex items-center justify-between p-4 border-b border-slate-200 dark:border-slate-700 flex-shrink-0">
                    <h3 class="text-lg font-bold text-slate-800 dark:text-white flex items-center gap-2">
                        <svg class="w-5 h-5 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                        Leasing Alerts <span id="leasing-modal-count" class="text-sm font-semibold text-white bg-amber-500 rounded-full px-2 py-0.5">0</span>
                    </h3>
                    <button onclick="leasingAlerts.close()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 text-2xl leading-none">&times;</button>
                </div>
                <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-700/30 flex-shrink-0">
                    <div class="flex flex-wrap gap-4 items-start">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Properties</label>
                            <div id="leasing-property-filter" class="relative"></div>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Category</label>
                            <div id="leasing-category-filter" class="relative"></div>
                        </div>
                    </div>
                </div>
                <div class="p-4 overflow-auto flex-1 min-h-0">
                    <table class="min-w-full text-sm">
                        <thead id="leasing-thead" class="bg-slate-50 dark:bg-slate-700/50"></thead>
                        <tbody id="leasing-tbody" class="divide-y divide-slate-100 dark:divide-slate-700"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <script>
        const propertiesData = {js_data};
        const propertyTableData = {table_data_js};
        const propertyGroups = {property_groups_js};
        const trafficData = {traffic_data_js};
        const leasesData = {leases_data_js};
        const trafficMonth = {month_str_js};
        const financialData = {financial_data_js};

        let statusChart;

        // Theme handling
        const themeToggleDarkIcon = document.getElementById('theme-toggle-dark-icon');
        const themeToggleLightIcon = document.getElementById('theme-toggle-light-icon');

        if (localStorage.getItem('color-theme') === 'dark' || (!localStorage.getItem('color-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)) {{
            themeToggleLightIcon.classList.remove('hidden');
            document.documentElement.classList.add('dark');
        }} else {{
            themeToggleDarkIcon.classList.remove('hidden');
        }}

        document.getElementById('theme-toggle').addEventListener('click', function() {{
            themeToggleDarkIcon.classList.toggle('hidden');
            themeToggleLightIcon.classList.toggle('hidden');
            document.documentElement.classList.toggle('dark');
            localStorage.setItem('color-theme', document.documentElement.classList.contains('dark') ? 'dark' : 'light');
            updateDashboard();
        }});

        // Toggle month expand/collapse
        function toggleMonth(monthKey) {{
            const rows = document.querySelectorAll(`.month-content-${{monthKey}}`);
            const chevron = document.getElementById(`chevron-${{monthKey}}`);
            const isHidden = rows[0]?.style.display === 'none';

            rows.forEach(row => {{
                row.style.display = isHidden ? 'table-row' : 'none';
            }});

            if (chevron) {{
                chevron.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
            }}
        }}

        // Pop-up modal functions
        let popupData = [];
        let popupFullData = [];  // Unfiltered data
        let popupSortColumn = '';
        let popupSortAsc = true;
        let popupColumns = [];
        let popupActiveFilters = new Set();  // Active status filters
        let popupHasFilters = false;  // Whether this popup has filters
        let popupAlertColumnKey = null;  // Which column shows the alert dot; null = last column

        function showPopup(category, abbr, monthKey, preFilter) {{
            popupAlertColumnKey = (category === 'applicants') ? 'status' : null;
            const modal = document.getElementById('popup-modal');
            const title = document.getElementById('popup-title');
            const p = propertyTableData[abbr];
            const l = leasesData[abbr] || {{}};

            let data = [];
            let columns = [];
            let titleText = '';

            // Define columns and get data based on category
            switch(category) {{
                case 'vacantReady':
                    data = p.vacantReadyList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'lastMoDate', label: 'Last MO'}},
                        {{key: 'daysVacant', label: 'Days Vacant'}},
                        {{key: 'avDate', label: 'Av. Date'}}
                    ];
                    titleText = `${{abbr}} - Vacant Ready (${{data.length}})`;
                    break;
                case 'vacantNotReady':
                    data = p.vacantNotReadyList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'lastMoDate', label: 'Last MO'}},
                        {{key: 'daysVacant', label: 'Days Vacant'}},
                        {{key: 'avDate', label: 'Av. Date'}}
                    ];
                    titleText = `${{abbr}} - Vacant Not Ready (${{data.length}})`;
                    break;
                case 'ntv':
                    data = p.ntvList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'ntvDate', label: 'NTV Date'}},
                        {{key: 'moDate', label: 'MO Date'}},
                        {{key: 'avDate', label: 'Av. Date'}}
                    ];
                    titleText = `${{abbr}} - NTV Units (${{data.length}})`;
                    break;
                case 'eviction':
                    data = p.evictionList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'name', label: 'Name'}},
                        {{key: 'leaseEnd', label: 'Lease End'}},
                        {{key: 'moDate', label: 'MO Date'}}
                    ];
                    titleText = `${{abbr}} - Under Eviction (${{data.length}})`;
                    break;
                case 'vacantLeased':
                    data = p.vacantLeasedList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'applicant', label: 'Applicant'}},
                        {{key: 'moveIn', label: 'Move-In'}}
                    ];
                    titleText = `${{abbr}} - Vacant Leased (${{data.length}})`;
                    break;
                case 'ntvLeased':
                    data = p.ntvLeasedList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'applicant', label: 'Applicant'}},
                        {{key: 'moveIn', label: 'Move-In'}}
                    ];
                    titleText = `${{abbr}} - NTV Leased (${{data.length}})`;
                    break;
                case 'offMarket':
                    data = p.offMarketList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'vacancyStatus', label: 'Vacancy Status'}},
                        {{key: 'avDate', label: 'Av. Date'}}
                    ];
                    titleText = `${{abbr}} - Off Market (${{data.length}})`;
                    break;
                case 'futureMI':
                    data = p.futureMIList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'resident', label: 'Resident'}},
                        {{key: 'signed', label: 'Signed Date'}},
                        {{key: 'moveIn', label: 'Move-In'}}
                    ];
                    titleText = `${{abbr}} - Future Move-Ins (${{data.length}})`;
                    break;
                case 'futureMO':
                    data = p.futureMOList || [];
                    columns = [
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'resident', label: 'Resident'}},
                        {{key: 'ntvDate', label: 'NTV Date'}},
                        {{key: 'moDate', label: 'MO Date'}}
                    ];
                    titleText = `${{abbr}} - Future Move-Outs (${{data.length}})`;
                    break;
                case 'applicants':
                    // Filter out transfers - they only appear in Vacant/NTV Leased popups
                    data = (l.applicantsList || []).filter(a => !a.isTransfer);
                    columns = [
                        {{key: 'status', label: 'Status'}},
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'applicant', label: 'Applicant'}},
                        {{key: 'appDate', label: 'App Date'}},
                        {{key: 'approvedDate', label: 'Approved'}},
                        {{key: 'signedDate', label: 'Signed'}},
                        {{key: 'moveIn', label: 'Move-In'}}
                    ];
                    titleText = `${{abbr}} - Applicants (${{data.length}})`;
                    break;
                case 'applicantsMtd':
                    data = l.applicantsMtdList || [];
                    columns = [
                        {{key: 'status', label: 'Status'}},
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'applicant', label: 'Applicant'}},
                        {{key: 'appDate', label: 'App Date'}},
                        {{key: 'approvedDate', label: 'Approved'}},
                        {{key: 'denialDate', label: 'Denied'}},
                        {{key: 'cancellationDate', label: 'Cancelled'}},
                        {{key: 'signedDate', label: 'Signed'}}
                    ];
                    titleText = `${{abbr}} - Applicants MTD (${{data.length}})`;
                    break;
                case 'expiring':
                    const expiring = l.expiring || {{}};
                    const lists = expiring.expiringLists || {{}};
                    data = lists[monthKey] || [];
                    const monthLabel = expiring[monthKey + '_label'] || monthKey;
                    columns = [
                        {{key: 'status', label: 'Status'}},
                        {{key: 'unit', label: 'Unit'}},
                        {{key: 'type', label: 'Type'}},
                        {{key: 'resident', label: 'Resident'}},
                        {{key: 'renewalSigned', label: 'Lease Signed'}},
                        {{key: 'ntvDate', label: 'NTV Date'}},
                        {{key: 'moDate', label: 'Move Out'}},
                        {{key: 'leaseEnd', label: 'Lease End'}}
                    ];
                    titleText = `${{abbr}} - Expiring ${{monthLabel}} (${{data.length}})`;
                    break;
                default:
                    return;
            }}

            if (data.length === 0) {{
                alert('No data available');
                return;
            }}

            popupData = data;
            popupColumns = columns;
            popupSortColumn = '';
            popupSortAsc = true;
            popupFullData = [...data];  // Store full unfiltered data

            // Set up filters for applicants and expiring
            const filtersContainer = document.getElementById('popup-filters');

            if (category === 'applicants' || category === 'expiring' || category === 'applicantsMtd') {{
                popupHasFilters = true;
                popupActiveFilters = new Set();  // empty = all statuses (alerts convention)
                if (preFilter) popupActiveFilters.add(preFilter);
                popupStatusDropdownOpen = false;
                renderPopupStatusFilter();
                filtersContainer.classList.remove('hidden');
            }} else {{
                popupHasFilters = false;
                popupActiveFilters.clear();
                popupStatusDropdownOpen = false;
                filtersContainer.classList.add('hidden');
            }}

            title.textContent = titleText;
            renderPopupTable();
            modal.classList.remove('hidden');
            document.body.style.overflow = 'hidden';
        }}

        const POPUP_STATUS_COLORS = {{
            'Pending': 'text-amber-600 dark:text-amber-400',
            'Approved': 'text-cyan-600 dark:text-cyan-400',
            'Signed': 'text-emerald-600 dark:text-emerald-400',
            'Current': 'text-slate-600 dark:text-slate-400',
            'Pending Renewal': 'text-amber-600 dark:text-amber-400',
            'Renewed': 'text-emerald-600 dark:text-emerald-400',
            'NTV': 'text-orange-600 dark:text-orange-400',
            'Former': 'text-orange-600 dark:text-orange-400',
            'Evicted': 'text-red-600 dark:text-red-400',
            'Under Eviction': 'text-red-600 dark:text-red-400',
            'Denied': 'text-rose-600 dark:text-rose-400',
            'Cancelled': 'text-slate-500 dark:text-slate-400',
            'Still Active': 'text-amber-600 dark:text-amber-400'
        }};

        let popupStatusDropdownOpen = false;

        function renderPopupStatusFilter() {{
            const container = document.getElementById('popup-status-filter');
            if (!container) return;
            const statuses = [...new Set(popupFullData.map(d => d.status).filter(s => s))];
            const selectedCount = popupActiveFilters.size;
            const buttonLabel = selectedCount === 0
                ? 'All Statuses'
                : (selectedCount === 1 ? Array.from(popupActiveFilters)[0] : `${{selectedCount}} selected`);
            const chevron = popupStatusDropdownOpen ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7';

            function checkboxCell(isChecked) {{
                const base = 'relative inline-flex items-center justify-center w-4 h-4 rounded border-2 transition-colors flex-shrink-0';
                const stateCls = isChecked
                    ? 'border-cyan-500 bg-cyan-500/10 dark:bg-cyan-400/10'
                    : 'border-slate-300 dark:border-slate-500 bg-transparent group-hover:border-slate-400 dark:group-hover:border-slate-400';
                const check = isChecked
                    ? '<svg class="w-3 h-3 text-cyan-500" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>'
                    : '';
                return `<span class="${{base}} ${{stateCls}}">${{check}}</span>`;
            }}

            function rowCls(isSelected) {{
                const base = 'group flex items-center gap-2.5 px-3 py-2 cursor-pointer text-sm whitespace-nowrap transition-colors';
                const bg = isSelected
                    ? 'bg-cyan-50/60 dark:bg-cyan-900/15 hover:bg-cyan-50 dark:hover:bg-cyan-900/25 text-slate-800 dark:text-slate-100'
                    : 'hover:bg-slate-50 dark:hover:bg-slate-700/40 text-slate-700 dark:text-slate-200';
                return `${{base}} ${{bg}}`;
            }}

            let dropdownHtml = '';
            if (popupStatusDropdownOpen) {{
                const allSelected = popupActiveFilters.size === 0;
                dropdownHtml = `<div class="absolute z-20 mt-1 w-max min-w-[14rem] max-w-[90vw] rounded-xl border-2 border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 shadow-xl max-h-80 overflow-y-auto overflow-x-hidden">`
                    + `<label class="${{rowCls(allSelected)}} font-medium border-b border-slate-200 dark:border-slate-600">`
                    + checkboxCell(allSelected)
                    + `<input type="checkbox" ${{allSelected ? 'checked' : ''}} onchange="selectAllPopupStatuses()" class="sr-only">`
                    + `<span>All Statuses</span>`
                    + `</label>`;
                statuses.forEach(status => {{
                    const isSelected = popupActiveFilters.has(status);
                    const color = POPUP_STATUS_COLORS[status] || 'text-slate-600 dark:text-slate-300';
                    const count = popupFullData.filter(d => d.status === status).length;
                    dropdownHtml += `<label class="${{rowCls(isSelected)}}">`
                        + checkboxCell(isSelected)
                        + `<input type="checkbox" ${{isSelected ? 'checked' : ''}} onchange="togglePopupStatus('${{status.replace(/'/g, "\\\\'")}}')" class="sr-only">`
                        + `<span class="font-medium ${{color}}">${{status}}</span>`
                        + `<span class="text-slate-400 dark:text-slate-500 text-xs ml-auto">${{count}}</span>`
                        + `</label>`;
                }});
                dropdownHtml += `</div>`;
            }}
            container.innerHTML = `<button type="button" onclick="togglePopupStatusDropdown()" class="flex items-center justify-between w-56 h-10 rounded-xl border-2 border-slate-200 dark:border-slate-600 py-2 pl-4 pr-3 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-medium cursor-pointer text-sm hover:border-slate-300 dark:hover:border-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400 transition-all">`
                + `<span class="truncate">${{buttonLabel}}</span>`
                + `<svg class="w-5 h-5 text-slate-400 ml-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${{chevron}}"></path></svg>`
                + `</button>${{dropdownHtml}}`;
        }}

        function togglePopupStatusDropdown() {{
            popupStatusDropdownOpen = !popupStatusDropdownOpen;
            renderPopupStatusFilter();
        }}

        function togglePopupStatus(status) {{
            if (popupActiveFilters.has(status)) popupActiveFilters.delete(status);
            else popupActiveFilters.add(status);
            renderPopupStatusFilter();
            applyPopupFilters();
        }}

        function selectAllPopupStatuses() {{
            popupActiveFilters.clear();
            renderPopupStatusFilter();
            applyPopupFilters();
        }}

        function applyPopupFilters() {{
            if (popupHasFilters && popupActiveFilters.size > 0) {{
                popupData = popupFullData.filter(d => popupActiveFilters.has(d.status));
            }} else {{
                popupData = [...popupFullData];  // empty set = show all
            }}
            // Re-apply current sort (two toggles = preserve direction while re-sorting)
            if (popupSortColumn) {{
                sortPopupTable(popupSortColumn);
                sortPopupTable(popupSortColumn);
            }} else {{
                renderPopupTable();
            }}
            // Update title count
            const title = document.getElementById('popup-title');
            const baseTitle = title.textContent.replace(/\(\d+\)/, '').trim();
            title.textContent = `${{baseTitle}} (${{popupData.length}})`;
        }}

        function formatDate(dateStr) {{
            // Convert YYYY-MM-DD to MM/DD/YYYY
            if (!dateStr || dateStr.length < 10) return dateStr || '';
            const parts = dateStr.substring(0, 10).split('-');
            if (parts.length !== 3) return dateStr;
            return `${{parts[1]}}/${{parts[2]}}/${{parts[0]}}`;
        }}

        // Date columns that need formatting
        const dateColumns = ['avDate', 'ntvDate', 'moDate', 'lastMoDate', 'leaseEnd', 'moveIn', 'signed', 'appDate', 'approvedDate', 'signedDate', 'renewalSigned', 'denialDate', 'cancellationDate'];

        function renderPopupTable() {{
            const thead = document.getElementById('popup-thead');
            const tbody = document.getElementById('popup-tbody');

            // Build header with sort indicators
            let headerHtml = '<tr>';
            popupColumns.forEach(col => {{
                const sortIcon = popupSortColumn === col.key ? (popupSortAsc ? ' ▲' : ' ▼') : '';
                headerHtml += `<th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-600 whitespace-nowrap" onclick="sortPopupTable('${{col.key}}')">${{col.label}}${{sortIcon}}</th>`;
            }});
            headerHtml += '</tr>';
            thead.innerHTML = headerHtml;

            // Build body
            let bodyHtml = '';
            popupData.forEach((row, idx) => {{
                const rowBg = idx % 2 === 0 ? '' : 'bg-slate-50/50 dark:bg-slate-800/30';
                const boldClass = row.hasAlert ? 'font-semibold' : '';
                bodyHtml += `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                popupColumns.forEach((col, colIdx) => {{
                    let val = row[col.key] || '';
                    // Format date columns
                    if (dateColumns.includes(col.key)) {{
                        val = formatDate(val);
                    }}
                    // Early-termination dot after Move Out date (NTV + Former only)
                    if (col.key === 'moDate' && row.isEarlyTermination) {{
                        val = `${{val}} <span class="inline-block w-[7px] h-[7px] rounded-full bg-red-500 ml-1 align-middle" title="Early Termination — move-out before lease end"></span>`;
                    }}
                    // Alert/Transfer indicator — on configured column (or last column by default)
                    const isAlertCol = popupAlertColumnKey
                        ? col.key === popupAlertColumnKey
                        : (colIdx === popupColumns.length - 1);
                    const hasRowAlert = isAlertCol && row.hasAlert;
                    const isTransfer = isAlertCol && row.isTransfer;
                    let indicatorHtml = '';
                    const flipRight = isAlertCol && colIdx < popupColumns.length - 1;
                    const tooltipDirCls = flipRight ? ' alert-tooltip-right' : '';
                    if (hasRowAlert) {{
                        const ageSuffix = (typeof row.daysAgo === 'number') ? ` (${{row.daysAgo}}d)` : '';
                        const pushedSuffix = (row.autoPushed && row.autoPushed.applied) ? ` – Pushed to ${{formatDate(row.autoPushed.newAvDate)}}` : '';
                        indicatorHtml = `<span class="alert-dot alert-tooltip${{tooltipDirCls}}" data-tooltip="${{(row.alertMsg || 'Needs attention') + ageSuffix + pushedSuffix}}"></span>`;
                    }} else if (isTransfer) {{
                        indicatorHtml = `<span class="transfer-dot alert-tooltip${{tooltipDirCls}}" data-tooltip="Transfer"></span>`;
                    }}
                    const posClass = (hasRowAlert || isTransfer) ? 'relative' : '';

                    // Make unit column a clickable link to ResMan if unitId exists
                    if (col.key === 'unit' && row.unitId) {{
                        const resmanUrl = `https://sns.myresman.com/#/Units/Detail/${{row.unitId}}`;
                        bodyHtml += `<td class="px-3 py-2 whitespace-nowrap ${{posClass}} ${{boldClass}}"><a href="${{resmanUrl}}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:scale-105 inline-block transition-transform ${{boldClass}}">${{val}}</a>${{indicatorHtml}}</td>`;
                    // Make applicant/resident column a clickable link to ResMan if leaseId exists
                    }} else if ((col.key === 'applicant' || col.key === 'resident' || col.key === 'name') && row.leaseId && val) {{
                        const resmanUrl = `https://sns.myresman.com/#/Residents/Detail/${{row.leaseId}}`;
                        bodyHtml += `<td class="px-3 py-2 whitespace-nowrap ${{posClass}} ${{boldClass}}"><a href="${{resmanUrl}}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:scale-105 inline-block transition-transform ${{boldClass}}">${{val}}</a>${{indicatorHtml}}</td>`;
                    }} else {{
                        bodyHtml += `<td class="px-3 py-2 text-slate-700 dark:text-slate-300 whitespace-nowrap ${{posClass}} ${{boldClass}}">${{val}}${{indicatorHtml}}</td>`;
                    }}
                }});
                bodyHtml += '</tr>';
            }});
            tbody.innerHTML = bodyHtml;
        }}

        function sortPopupTable(column) {{
            if (popupSortColumn === column) {{
                popupSortAsc = !popupSortAsc;
            }} else {{
                popupSortColumn = column;
                popupSortAsc = true;
            }}

            popupData.sort((a, b) => {{
                let valA = a[column] || '';
                let valB = b[column] || '';

                // Date columns - sort by original YYYY-MM-DD format
                if (dateColumns.includes(column)) {{
                    // Empty dates go to end
                    if (!valA && !valB) return 0;
                    if (!valA) return popupSortAsc ? 1 : -1;
                    if (!valB) return popupSortAsc ? -1 : 1;
                    // Compare as strings (YYYY-MM-DD sorts correctly)
                    if (valA < valB) return popupSortAsc ? -1 : 1;
                    if (valA > valB) return popupSortAsc ? 1 : -1;
                    return 0;
                }}

                // Try numeric sort first
                const numA = parseFloat(valA);
                const numB = parseFloat(valB);
                if (!isNaN(numA) && !isNaN(numB)) {{
                    return popupSortAsc ? numA - numB : numB - numA;
                }}

                // String sort
                valA = String(valA).toLowerCase();
                valB = String(valB).toLowerCase();
                if (valA < valB) return popupSortAsc ? -1 : 1;
                if (valA > valB) return popupSortAsc ? 1 : -1;
                return 0;
            }});

            renderPopupTable();
        }}

        function closePopup() {{
            popupStatusDropdownOpen = false;
            document.getElementById('popup-modal').classList.add('hidden');
            document.body.style.overflow = '';
        }}

        // ============ Alerts popups (ops bell + leasing icon) ============
        const ALERT_CATEGORY_LABELS = {{
            vacantNotReadyList: 'Vacant – Not Ready',
            ntvList: 'NTV',
            vacantLeasedList: 'Vacant – Leased',
            insufficientNoticeList: 'Insufficient Notice',
            pendingInRealUnitList: 'Pending – Real Unit',
            approvedPaidInHoldingList: 'Approved + Paid in Holding'
        }};

        const ALERTS_COLUMNS = [
            {{key: 'abbr',       label: 'Property'}},
            {{key: 'category',   label: 'Category'}},
            {{key: 'unit',       label: 'Unit'}},
            {{key: 'resident',   label: 'Resident / Applicant'}},
            {{key: 'alertMsg',   label: 'Alert'}},
            {{key: 'daysAgo',    label: 'Age'}}
        ];

        function compareAlertVals(a, b, col) {{
            const va = a[col], vb = b[col];
            const numA = typeof va === 'number' ? va : null;
            const numB = typeof vb === 'number' ? vb : null;
            if (numA !== null || numB !== null) {{
                const ax = numA === null ? Number.NEGATIVE_INFINITY : numA;
                const bx = numB === null ? Number.NEGATIVE_INFINITY : numB;
                return ax - bx;
            }}
            return String(va || '').localeCompare(String(vb || ''), undefined, {{sensitivity: 'base'}});
        }}

        function createAlertsPopup(cfg) {{
            // cfg: {{ keys, badgeId, modalId, theadId, tbodyId, countId, filterContainerId, categoryFilterContainerId?, dotColor, filterBtnColor }}
            const state = {{
                sortColumn: 'daysAgo',
                sortAsc: false,
                selected: new Set(),  // empty = all properties
                dropdownOpen: false,
                filterRendered: false,
                selectedCategories: new Set(),  // empty = all categories
                categoryDropdownOpen: false
            }};

            function collect() {{
                const out = [];
                Object.keys(propertyTableData).forEach(abbr => {{
                    const p = propertyTableData[abbr];
                    if (!p) return;
                    cfg.keys.forEach(listKey => {{
                        const items = p[listKey] || [];
                        items.forEach(item => {{
                            if (!item.hasAlert) return;
                            out.push({{
                                abbr: abbr,
                                propertyName: p.name || abbr,
                                category: ALERT_CATEGORY_LABELS[listKey],
                                unit: item.unit || '',
                                unitId: item.unitId || '',
                                resident: item.applicant || item.name || item.resident || '',
                                leaseId: item.leaseId || '',
                                alertMsg: item.alertMsg || 'Needs attention',
                                firstSeen: item.firstSeen || '',
                                daysAgo: (typeof item.daysAgo === 'number') ? item.daysAgo : null,
                                autoPushed: item.autoPushed || null
                            }});
                        }});
                    }});
                }});
                return out;
            }}

            function updateBadge() {{
                const alerts = collect();
                const badge = document.getElementById(cfg.badgeId);
                if (!badge) return;
                if (alerts.length > 0) {{
                    badge.textContent = alerts.length > 99 ? '99+' : String(alerts.length);
                    badge.classList.remove('hidden');
                }} else {{
                    badge.classList.add('hidden');
                }}
            }}

            function renderFilter() {{
                const container = document.getElementById(cfg.filterContainerId);
                if (!container) return;
                const abbrs = Object.keys(propertyTableData).filter(a => a !== 'ALL').sort();
                const selectedCount = state.selected.size;
                const buttonLabel = selectedCount === 0
                    ? 'All Properties'
                    : (selectedCount === 1 ? Array.from(state.selected)[0] : `${{selectedCount}} selected`);
                const chevron = state.dropdownOpen ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7';

                function checkboxCell(isChecked) {{
                    const base = 'relative inline-flex items-center justify-center w-4 h-4 rounded border-2 transition-colors flex-shrink-0';
                    const stateCls = isChecked
                        ? 'border-cyan-500 bg-cyan-500/10 dark:bg-cyan-400/10'
                        : 'border-slate-300 dark:border-slate-500 bg-transparent group-hover:border-slate-400 dark:group-hover:border-slate-400';
                    const check = isChecked
                        ? '<svg class="w-3 h-3 text-cyan-500" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>'
                        : '';
                    return `<span class="${{base}} ${{stateCls}}">${{check}}</span>`;
                }}

                function rowCls(isSelected) {{
                    const base = 'group flex items-center gap-2.5 px-3 py-2 cursor-pointer text-sm whitespace-nowrap transition-colors';
                    const bg = isSelected
                        ? 'bg-cyan-50/60 dark:bg-cyan-900/15 hover:bg-cyan-50 dark:hover:bg-cyan-900/25 text-slate-800 dark:text-slate-100'
                        : 'hover:bg-slate-50 dark:hover:bg-slate-700/40 text-slate-700 dark:text-slate-200';
                    return `${{base}} ${{bg}}`;
                }}

                let dropdownHtml = '';
                if (state.dropdownOpen) {{
                    const allSelected = state.selected.size === 0;
                    dropdownHtml = `<div class="absolute z-20 mt-1 w-max min-w-[14rem] max-w-[90vw] rounded-xl border-2 border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 shadow-xl max-h-80 overflow-y-auto overflow-x-hidden">`
                        + `<label class="${{rowCls(allSelected)}} font-medium border-b border-slate-200 dark:border-slate-600">`
                        + checkboxCell(allSelected)
                        + `<input type="checkbox" ${{allSelected ? 'checked' : ''}} onchange="${{cfg.varName}}.selectAll()" class="sr-only">`
                        + `<span>All Properties</span>`
                        + `</label>`;
                    abbrs.forEach(abbr => {{
                        const p = propertyTableData[abbr];
                        const isSelected = state.selected.has(abbr);
                        dropdownHtml += `<label class="${{rowCls(isSelected)}}">`
                            + checkboxCell(isSelected)
                            + `<input type="checkbox" ${{isSelected ? 'checked' : ''}} onchange="${{cfg.varName}}.toggle('${{abbr}}')" class="sr-only">`
                            + `<span class="font-medium">${{abbr}}</span>`
                            + `<span class="text-slate-400 dark:text-slate-500 text-xs">${{p.name || ''}}</span>`
                            + `</label>`;
                    }});
                    dropdownHtml += `</div>`;
                }}
                container.innerHTML = `<button type="button" onclick="${{cfg.varName}}.toggleDropdown()" class="flex items-center justify-between w-64 h-10 rounded-xl border-2 border-slate-200 dark:border-slate-600 py-2 pl-4 pr-3 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-medium cursor-pointer text-sm hover:border-slate-300 dark:hover:border-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400 transition-all">`
                    + `<span class="truncate">${{buttonLabel}}</span>`
                    + `<svg class="w-5 h-5 text-slate-400 ml-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${{chevron}}"></path></svg>`
                    + `</button>${{dropdownHtml}}`;
            }}

            function allCategories() {{
                return cfg.keys.map(k => ALERT_CATEGORY_LABELS[k]).filter(Boolean);
            }}

            function renderCategoryFilter() {{
                if (!cfg.categoryFilterContainerId) return;
                const container = document.getElementById(cfg.categoryFilterContainerId);
                if (!container) return;
                const cats = allCategories();
                const selectedCount = state.selectedCategories.size;
                const buttonLabel = selectedCount === 0
                    ? 'All Categories'
                    : (selectedCount === 1 ? Array.from(state.selectedCategories)[0] : `${{selectedCount}} selected`);
                const chevron = state.categoryDropdownOpen ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7';

                function checkboxCell(isChecked) {{
                    const base = 'relative inline-flex items-center justify-center w-4 h-4 rounded border-2 transition-colors flex-shrink-0';
                    const stateCls = isChecked
                        ? 'border-cyan-500 bg-cyan-500/10 dark:bg-cyan-400/10'
                        : 'border-slate-300 dark:border-slate-500 bg-transparent group-hover:border-slate-400 dark:group-hover:border-slate-400';
                    const check = isChecked
                        ? '<svg class="w-3 h-3 text-cyan-500" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>'
                        : '';
                    return `<span class="${{base}} ${{stateCls}}">${{check}}</span>`;
                }}
                function rowCls(isSelected) {{
                    const base = 'group flex items-center gap-2.5 px-3 py-2 cursor-pointer text-sm whitespace-nowrap transition-colors';
                    const bg = isSelected
                        ? 'bg-cyan-50/60 dark:bg-cyan-900/15 hover:bg-cyan-50 dark:hover:bg-cyan-900/25 text-slate-800 dark:text-slate-100'
                        : 'hover:bg-slate-50 dark:hover:bg-slate-700/40 text-slate-700 dark:text-slate-200';
                    return `${{base}} ${{bg}}`;
                }}

                let dropdownHtml = '';
                if (state.categoryDropdownOpen) {{
                    const allSelected = state.selectedCategories.size === 0;
                    dropdownHtml = `<div class="absolute z-20 mt-1 w-max min-w-[14rem] max-w-[90vw] rounded-xl border-2 border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 shadow-xl max-h-80 overflow-y-auto overflow-x-hidden">`
                        + `<label class="${{rowCls(allSelected)}} font-medium border-b border-slate-200 dark:border-slate-600">`
                        + checkboxCell(allSelected)
                        + `<input type="checkbox" ${{allSelected ? 'checked' : ''}} onchange="${{cfg.varName}}.selectAllCategories()" class="sr-only">`
                        + `<span>All Categories</span>`
                        + `</label>`;
                    cats.forEach(cat => {{
                        const isSelected = state.selectedCategories.has(cat);
                        const safeCat = cat.replace(/'/g, "\\\\'");
                        dropdownHtml += `<label class="${{rowCls(isSelected)}}">`
                            + checkboxCell(isSelected)
                            + `<input type="checkbox" ${{isSelected ? 'checked' : ''}} onchange="${{cfg.varName}}.toggleCategory('${{safeCat}}')" class="sr-only">`
                            + `<span class="font-medium">${{cat}}</span>`
                            + `</label>`;
                    }});
                    dropdownHtml += `</div>`;
                }}
                container.innerHTML = `<button type="button" onclick="${{cfg.varName}}.toggleCategoryDropdown()" class="flex items-center justify-between w-64 h-10 rounded-xl border-2 border-slate-200 dark:border-slate-600 py-2 pl-4 pr-3 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-medium cursor-pointer text-sm hover:border-slate-300 dark:hover:border-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-cyan-400 transition-all">`
                    + `<span class="truncate">${{buttonLabel}}</span>`
                    + `<svg class="w-5 h-5 text-slate-400 ml-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${{chevron}}"></path></svg>`
                    + `</button>${{dropdownHtml}}`;
            }}

            function renderHeader() {{
                const thead = document.getElementById(cfg.theadId);
                if (!thead) return;
                let h = '<tr>';
                ALERTS_COLUMNS.forEach(col => {{
                    const sortIcon = state.sortColumn === col.key ? (state.sortAsc ? ' ▲' : ' ▼') : '';
                    h += `<th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-600 whitespace-nowrap" onclick="${{cfg.varName}}.sort('${{col.key}}')">${{col.label}}${{sortIcon}}</th>`;
                }});
                h += '</tr>';
                thead.innerHTML = h;
            }}

            function ageBadge(a) {{
                if (a.daysAgo === null || a.daysAgo === undefined) return '<span class="text-slate-400">—</span>';
                const d = a.daysAgo;
                let cls = 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200';
                if (d >= 14) cls = 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200';
                else if (d >= 7) cls = 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200';
                const title = a.firstSeen ? `first seen ${{a.firstSeen}}` : '';
                return `<span title="${{title}}" class="inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${{cls}}">${{d}}d</span>`;
            }}

            function renderTable() {{
                renderHeader();
                renderFilter();
                renderCategoryFilter();
                const tbody = document.getElementById(cfg.tbodyId);
                const countChip = document.getElementById(cfg.countId);
                if (!tbody) return;
                const all = collect();
                const filtered = all.filter(a =>
                    (state.selected.size === 0 || state.selected.has(a.abbr)) &&
                    (state.selectedCategories.size === 0 || state.selectedCategories.has(a.category))
                );
                filtered.sort((a, b) => {{
                    const cmp = compareAlertVals(a, b, state.sortColumn);
                    return state.sortAsc ? cmp : -cmp;
                }});
                if (countChip) countChip.textContent = String(filtered.length);
                if (filtered.length === 0) {{
                    tbody.innerHTML = '<tr><td colspan="6" class="px-3 py-6 text-center text-slate-500 dark:text-slate-400">No alerts</td></tr>';
                    return;
                }}
                const alertColorClasses = cfg.dotColor === 'amber'
                    ? 'bg-amber-600 dark:bg-amber-400'
                    : 'bg-red-600 dark:bg-red-400';
                const alertTextClasses = cfg.dotColor === 'amber'
                    ? 'text-amber-600 dark:text-amber-400'
                    : 'text-red-600 dark:text-red-400';
                let html = '';
                filtered.forEach(a => {{
                    const unitCell = a.unitId
                        ? `<a href="https://sns.myresman.com/#/Units/Detail/${{a.unitId}}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:scale-105 inline-block transition-transform">${{a.unit}}</a>`
                        : a.unit;
                    const residentCell = (a.leaseId && a.resident)
                        ? `<a href="https://sns.myresman.com/#/Residents/Detail/${{a.leaseId}}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:scale-105 inline-block transition-transform">${{a.resident}}</a>`
                        : (a.resident || '<span class="text-slate-400">—</span>');
                    const pushedBadge = (a.autoPushed && a.autoPushed.applied)
                        ? ` <span class="ml-1 inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200" title="Pushed to ${{formatDate(a.autoPushed.newAvDate)}}">Pushed to ${{formatDate(a.autoPushed.newAvDate)}}</span>`
                        : '';
                    html += `<tr class="hover:bg-slate-50 dark:hover:bg-slate-700/40">`
                        + `<td class="px-3 py-2 whitespace-nowrap font-medium text-slate-700 dark:text-slate-200">${{a.abbr}} <span class="text-slate-400 dark:text-slate-500 text-xs">${{a.propertyName}}</span></td>`
                        + `<td class="px-3 py-2 whitespace-nowrap text-slate-700 dark:text-slate-300">${{a.category}}</td>`
                        + `<td class="px-3 py-2 whitespace-nowrap">${{unitCell}}</td>`
                        + `<td class="px-3 py-2 whitespace-nowrap">${{residentCell}}</td>`
                        + `<td class="px-3 py-2 whitespace-nowrap"><span class="inline-flex items-center gap-1.5"><span class="inline-block w-[6px] h-[6px] rounded-full ${{alertColorClasses}}"></span><span class="${{alertTextClasses}} font-medium">${{a.alertMsg}}</span>${{pushedBadge}}</span></td>`
                        + `<td class="px-3 py-2 whitespace-nowrap">${{ageBadge(a)}}</td>`
                        + `</tr>`;
                }});
                tbody.innerHTML = html;
            }}

            return {{
                state: state,
                collect: collect,
                updateBadge: updateBadge,
                renderTable: renderTable,
                sort: function(col) {{
                    if (state.sortColumn === col) state.sortAsc = !state.sortAsc;
                    else {{ state.sortColumn = col; state.sortAsc = (col !== 'daysAgo'); }}
                    renderTable();
                }},
                toggleDropdown: function() {{
                    state.dropdownOpen = !state.dropdownOpen;
                    renderFilter();
                }},
                toggle: function(abbr) {{
                    if (state.selected.has(abbr)) state.selected.delete(abbr);
                    else state.selected.add(abbr);
                    renderTable();
                }},
                selectAll: function() {{
                    state.selected.clear();
                    renderTable();
                }},
                toggleCategoryDropdown: function() {{
                    state.categoryDropdownOpen = !state.categoryDropdownOpen;
                    renderCategoryFilter();
                }},
                toggleCategory: function(cat) {{
                    if (state.selectedCategories.has(cat)) state.selectedCategories.delete(cat);
                    else state.selectedCategories.add(cat);
                    renderTable();
                }},
                selectAllCategories: function() {{
                    state.selectedCategories.clear();
                    renderTable();
                }},
                show: function() {{
                    renderTable();
                    document.getElementById(cfg.modalId).classList.remove('hidden');
                    document.body.style.overflow = 'hidden';
                }},
                close: function() {{
                    state.dropdownOpen = false;
                    state.categoryDropdownOpen = false;
                    document.getElementById(cfg.modalId).classList.add('hidden');
                    document.body.style.overflow = '';
                }}
            }};
        }}

        const opsAlerts = createAlertsPopup({{
            varName: 'opsAlerts',
            keys: ['vacantNotReadyList', 'ntvList', 'vacantLeasedList'],
            badgeId: 'alerts-badge',
            modalId: 'alerts-modal',
            theadId: 'alerts-thead',
            tbodyId: 'alerts-tbody',
            countId: 'alerts-modal-count',
            filterContainerId: 'alerts-property-filter',
            categoryFilterContainerId: 'alerts-category-filter',
            dotColor: 'red'
        }});

        const leasingAlerts = createAlertsPopup({{
            varName: 'leasingAlerts',
            keys: ['insufficientNoticeList', 'pendingInRealUnitList', 'approvedPaidInHoldingList'],
            badgeId: 'leasing-badge',
            modalId: 'leasing-modal',
            theadId: 'leasing-thead',
            tbodyId: 'leasing-tbody',
            countId: 'leasing-modal-count',
            filterContainerId: 'leasing-property-filter',
            categoryFilterContainerId: 'leasing-category-filter',
            dotColor: 'amber'
        }});

        function updateAlertsBadge() {{
            opsAlerts.updateBadge();
            leasingAlerts.updateBadge();
        }}

        // Close popup on Escape key
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') {{
                closePopup();
                opsAlerts.close();
                leasingAlerts.close();
            }}
        }});

        function getColors() {{
            const isDark = document.documentElement.classList.contains('dark');
            return {{
                text: isDark ? '#94a3b8' : '#64748b',
                occupied: isDark ? '#34d399' : '#10b981',
                vacant: isDark ? '#60a5fa' : '#3b82f6',
                vacantLeased: isDark ? '#a78bfa' : '#8b5cf6',
                ntv: isDark ? '#fbbf24' : '#f59e0b',
                ntvLeased: isDark ? '#fb923c' : '#f97316',
                eviction: isDark ? '#f87171' : '#ef4444'
            }};
        }}

        function renderPortfolioTable() {{
            const table = document.getElementById('portfolio-matrix-table');

            // Get all properties in order by region
            const regions = Object.keys(propertyGroups);
            const allProps = [];
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    if (propertyTableData[abbr]) {{
                        allProps.push({{ abbr, region, ...propertyTableData[abbr] }});
                    }}
                }});
            }});

            // Calculate totals
            let totals = {{ total: 0, occupied: 0, vacantReady: 0, vacantNotReady: 0, vacantLeased: 0, ntv: 0, ntvLeased: 0, eviction: 0, offMarket: 0, futureMI: 0, futureMO: 0 }};
            let futureMovesMonth = '';
            allProps.forEach(p => {{
                totals.total += p.total || 0;
                totals.occupied += p.occupied || 0;
                totals.vacantReady += p.vacantReady || 0;
                totals.vacantNotReady += p.vacantNotReady || 0;
                totals.vacantLeased += p.vacantLeased || 0;
                totals.ntv += p.ntv || 0;
                totals.ntvLeased += p.ntvLeased || 0;
                totals.eviction += p.eviction || 0;
                totals.offMarket += p.offMarket || 0;
                totals.futureMI += p.futureMI || 0;
                totals.futureMO += p.futureMO || 0;
                if (p.futureMovesMonth) futureMovesMonth = p.futureMovesMonth;
            }});

            // Region colors
            const regionColors = {{
                'Tampa': 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
                'Orlando': 'bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
                'Jacksonville': 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300',
                'Ft. Lauderdale': 'bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'
            }};

            // Build header with region grouping - use fixed widths for alignment
            let headerRow1 = '<tr class="border-b border-slate-200 dark:border-slate-700"><th class="px-3 py-2 text-left font-bold text-slate-700 dark:text-slate-200 bg-slate-100 dark:bg-slate-700/50 sticky left-0 z-10 w-[140px]"></th>';
            let headerRow2 = '<tr class="border-b-2 border-slate-300 dark:border-slate-600"><th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/50 sticky left-0 z-10 w-[140px]">Status</th>';

            regions.forEach(region => {{
                const propsInRegion = propertyGroups[region].filter(a => propertyTableData[a]);
                const colspan = propsInRegion.length;
                if (colspan > 0) {{
                    headerRow1 += `<th colspan="${{colspan}}" class="px-2 py-2 text-center font-bold text-xs uppercase tracking-wide ${{regionColors[region] || 'bg-slate-100 dark:bg-slate-700'}}">${{region}}</th>`;
                    propsInRegion.forEach(abbr => {{
                        headerRow2 += `<th class="px-2 py-2 text-center w-[65px]">
                            <div class="font-bold text-slate-900 dark:text-white text-xs">${{abbr}}</div>
                        </th>`;
                    }});
                }}
            }});

            // Total column
            headerRow1 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">TOTAL</th></tr>';
            headerRow2 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">Portfolio</th></tr>';

            // Status rows - grouped logically
            // Top section: all units (leased included in ready/not ready counts)
            // Bottom section: breakdown of how many are leased
            const tip = (text) => `<span class="tip" data-tip="${{text}}">?</span>`;

            const statusRows = [
                {{ key: 'total', label: 'Total Units', color: 'text-slate-900 dark:text-white font-bold', getValue: p => p.total }},
                {{ key: 'occupied', label: 'Occupied', color: 'text-emerald-600 dark:text-emerald-400', getValue: p => p.occupied }},
                {{ key: 'vacantReady', label: 'Vacant Ready' + tip('Ready for move-in (includes pre-leased)'), color: 'text-blue-600 dark:text-blue-400', getValue: p => p.vacantReady, clickable: true }},
                {{ key: 'vacantNotReady', label: 'Vacant Not Ready' + tip('Not ready yet (includes pre-leased)'), color: 'text-cyan-600 dark:text-cyan-400', getValue: p => p.vacantNotReady, clickable: true }},
                {{ key: 'ntv', label: 'NTV' + tip('Notice to Vacate (includes pre-leased)'), color: 'text-amber-600 dark:text-amber-400', getValue: p => p.ntv, clickable: true }},
                {{ key: 'eviction', label: 'Under Eviction', color: 'text-red-600 dark:text-red-400', getValue: p => p.eviction, clickable: true }},
                {{ key: 'occPhysical', label: 'Physical Occupancy' + tip('Currently occupied ÷ Total units'), color: '', getValue: p => ((p.total - (p.vacantReady || 0) - (p.vacantNotReady || 0)) / p.total * 100).toFixed(2), isOccupancy: true }},
                {{ key: 'divider', label: '', color: '', getValue: () => '', isDivider: true }},
                {{ key: 'vacantLeased', label: 'Vacant Leased', color: 'text-indigo-600 dark:text-indigo-400', getValue: p => p.vacantLeased, clickable: true }},
                {{ key: 'ntvLeased', label: 'NTV Leased', color: 'text-orange-600 dark:text-orange-400', getValue: p => p.ntvLeased, clickable: true }},
                {{ key: 'availableForRent', label: 'Rentable Units' + tip('Vacant + NTV minus pre-leased'), color: 'text-blue-700 dark:text-blue-300 font-semibold', getValue: p => ((p.vacantReady || 0) + (p.vacantNotReady || 0) + (p.ntv || 0)) - ((p.vacantLeased || 0) + (p.ntvLeased || 0)) }},
                {{ key: 'offMarket', label: 'Off the Market' + tip('Available units not listed online'), color: 'text-pink-600 dark:text-pink-400', getValue: p => p.offMarket, clickable: true }},
                {{ key: 'occRented', label: 'Occupancy Rented' + tip('Physical + pre-leased vacant units'), color: '', getValue: p => ((p.total - (p.vacantReady || 0) - (p.vacantNotReady || 0) + (p.vacantLeased || 0)) / p.total * 100).toFixed(2), isOccupancy: true }},
                {{ key: 'divider2', label: '', color: '', getValue: () => '', isDivider: true }},
                {{ key: 'futureMI', label: `Future MI (${{futureMovesMonth}})` + tip('Signed pending leases moving in this month'), color: 'text-emerald-600 dark:text-emerald-400', getValue: p => p.futureMI || 0, clickable: true }},
                {{ key: 'futureMO', label: `Future MO (${{futureMovesMonth}})` + tip('NTV units moving out this month'), color: 'text-amber-600 dark:text-amber-400', getValue: p => p.futureMO || 0, clickable: true }},
                {{ key: 'projectedOcc', label: 'Projected Occupancy' + tip('Physical Occ adjusted for future moves'), color: '', getValue: p => ((p.total - (p.vacantReady || 0) - (p.vacantNotReady || 0) + (p.futureMI || 0) - (p.futureMO || 0)) / p.total * 100).toFixed(2), isOccupancy: true }}
            ];

            let bodyRows = '';
            statusRows.forEach((row, idx) => {{
                // Handle divider row
                if (row.isDivider) {{
                    const colCount = allProps.length + 2; // +2 for label and total columns
                    bodyRows += `<tr class="h-2 bg-slate-200 dark:bg-slate-600"><td colspan="${{colCount}}"></td></tr>`;
                    return;
                }}

                const isAlt = idx % 2 === 1;
                const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                let tr = `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                tr += `<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700">${{row.label}}</td>`;

                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const p = propertyTableData[abbr];
                        if (p) {{
                            let val = row.getValue(p) || 0;
                            let cellColor = row.color;

                            if (row.isOccupancy) {{
                                val = val + '%';
                                const occVal = parseFloat(row.getValue(p));
                                cellColor = occVal >= 95 ? 'text-emerald-600 dark:text-emerald-400 font-bold' :
                                           occVal >= 90 ? 'text-amber-600 dark:text-amber-400 font-semibold' :
                                           'text-red-600 dark:text-red-400 font-semibold';
                            }}

                            // Check for alerts - use background color
                            let hasAlert = false;
                            if (row.key === 'vacantNotReady' && p.vacantNotReadyAlerts > 0) {{
                                hasAlert = true;
                            }} else if (row.key === 'ntv' && p.ntvAlerts > 0) {{
                                hasAlert = true;
                            }} else if (row.key === 'vacantLeased' && p.vacantLeasedAlerts > 0) {{
                                hasAlert = true;
                            }}

                            const alertBg = hasAlert ? 'bg-red-100 dark:bg-blue-900/40' : '';
                            if (row.clickable && val > 0) {{
                                tr += `<td class="px-2 py-2 text-center ${{cellColor}} ${{alertBg}} cursor-pointer hover:bg-blue-100 dark:hover:bg-blue-900/50" onclick="showPopup('${{row.key}}', '${{abbr}}')">${{val}}</td>`;
                            }} else {{
                                tr += `<td class="px-2 py-2 text-center ${{cellColor}} ${{alertBg}}">${{val}}</td>`;
                            }}
                        }}
                    }});
                }});

                // Total column
                let totalVal;
                if (row.isDivider) totalVal = '';
                else if (row.key === 'total') totalVal = totals.total;
                else if (row.key === 'occupied') totalVal = totals.occupied;
                else if (row.key === 'vacantReady') totalVal = totals.vacantReady;
                else if (row.key === 'vacantNotReady') totalVal = totals.vacantNotReady;
                else if (row.key === 'vacantLeased') totalVal = totals.vacantLeased;
                else if (row.key === 'ntv') totalVal = totals.ntv;
                else if (row.key === 'ntvLeased') totalVal = totals.ntvLeased;
                else if (row.key === 'eviction') totalVal = totals.eviction;
                else if (row.key === 'offMarket') totalVal = totals.offMarket;
                else if (row.key === 'availableForRent') totalVal = (totals.vacantReady + totals.vacantNotReady + totals.ntv) - (totals.vacantLeased + totals.ntvLeased);
                else if (row.key === 'occPhysical') totalVal = ((totals.total - totals.vacantReady - totals.vacantNotReady) / totals.total * 100).toFixed(2) + '%';
                else if (row.key === 'occRented') totalVal = ((totals.total - totals.vacantReady - totals.vacantNotReady + totals.vacantLeased) / totals.total * 100).toFixed(2) + '%';
                else if (row.key === 'futureMI') totalVal = totals.futureMI;
                else if (row.key === 'futureMO') totalVal = totals.futureMO;
                else if (row.key === 'projectedOcc') totalVal = ((totals.total - totals.vacantReady - totals.vacantNotReady + totals.futureMI - totals.futureMO) / totals.total * 100).toFixed(2) + '%';

                const totalColor = row.isOccupancy ? 'text-slate-900 dark:text-white font-bold' : row.color + ' font-bold';
                tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{totalColor}}">${{totalVal}}</td>`;
                tr += '</tr>';
                bodyRows += tr;
            }});

            table.innerHTML = `
                <thead class="bg-slate-50 dark:bg-slate-800/50">
                    ${{headerRow1}}
                    ${{headerRow2}}
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-700">
                    ${{bodyRows}}
                </tbody>
            `;
        }}

        function renderTrafficTables() {{
            document.getElementById('traffic-month').textContent = '(' + trafficMonth + ')';

            // Get all properties in order by region
            const regions = Object.keys(propertyGroups);
            const allProps = [];
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    allProps.push({{ abbr, region }});
                }});
            }});

            // Calculate totals (both sections aggregated here, used by respective rows)
            let totals = {{
                total: 0, dailyAvg: 0,
                signedLeasesMtd: 0,
                applicantsMtd: 0, applicantsMtdApproved: 0, applicantsMtdDenied: 0, applicantsMtdCancelled: 0, applicantsMtdActive: 0,
                totalApplicants: 0, pendingApproval: 0, approved: 0, signedLease: 0
            }};
            allProps.forEach(p => {{
                const t = trafficData[p.abbr] || {{}};
                const l = leasesData[p.abbr] || {{}};
                totals.total += t.total || 0;
                totals.dailyAvg += t.daily_avg || 0;
                totals.signedLeasesMtd += l.signed_leases_mtd || 0;
                totals.applicantsMtd += l.applicants_mtd || 0;
                totals.applicantsMtdApproved += l.applicants_mtd_approved || 0;
                totals.applicantsMtdDenied += l.applicants_mtd_denied || 0;
                totals.applicantsMtdCancelled += l.applicants_mtd_cancelled || 0;
                totals.applicantsMtdActive += l.applicants_mtd_active || 0;
                totals.totalApplicants += l.total_applicants || 0;
                totals.pendingApproval += l.pending_approval || 0;
                totals.approved += l.approved || 0;
                totals.signedLease += l.signed_lease || 0;
            }});

            // Region colors
            const regionColors = {{
                'Tampa': 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
                'Orlando': 'bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
                'Jacksonville': 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300',
                'Ft. Lauderdale': 'bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'
            }};
            const tip = (text) => `<span class="tip" data-tip="${{text}}">?</span>`;

            function buildHeader() {{
                let h1 = '<tr class="border-b border-slate-200 dark:border-slate-700"><th class="px-3 py-2 text-left font-bold text-slate-700 dark:text-slate-200 bg-slate-100 dark:bg-slate-700/50 sticky left-0 z-10 w-[140px]"></th>';
                let h2 = '<tr class="border-b-2 border-slate-300 dark:border-slate-600"><th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/50 sticky left-0 z-10 w-[140px]">Metric</th>';
                regions.forEach(region => {{
                    const propsInRegion = propertyGroups[region];
                    const colspan = propsInRegion.length;
                    if (colspan > 0) {{
                        h1 += `<th colspan="${{colspan}}" class="px-2 py-2 text-center font-bold text-xs uppercase tracking-wide ${{regionColors[region] || 'bg-slate-100 dark:bg-slate-700'}}">${{region}}</th>`;
                        propsInRegion.forEach(abbr => {{
                            h2 += `<th class="px-2 py-2 text-center w-[65px]"><div class="font-bold text-slate-900 dark:text-white text-xs">${{abbr}}</div></th>`;
                        }});
                    }}
                }});
                h1 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">TOTAL</th></tr>';
                h2 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">Portfolio</th></tr>';
                return h1 + h2;
            }}

            function renderSection(tableId, rows) {{
                const table = document.getElementById(tableId);
                if (!table) return;
                const colCount = allProps.length + 2;
                let bodyRows = '';
                rows.forEach((row, idx) => {{
                    if (row.isDivider) {{
                        bodyRows += `<tr class="h-2 bg-slate-200 dark:bg-slate-600"><td colspan="${{colCount}}"></td></tr>`;
                        return;
                    }}
                    const isAlt = idx % 2 === 1;
                    const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                    let tr = `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                    tr += `<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700">${{row.label}}</td>`;
                    regions.forEach(region => {{
                        propertyGroups[region].forEach(abbr => {{
                            const val = row.getValue(abbr);
                            const suffix = row.isPercent ? '%' : '';
                            const alertInfo = row.getAlert ? row.getAlert(abbr) : null;
                            const alertBg = alertInfo ? 'bg-red-100 dark:bg-blue-900/40' : '';
                            const isClickable = row.popupCategory && val > 0;
                            if (isClickable) {{
                                const filterArg = row.popupFilter ? `, null, '${{row.popupFilter}}'` : '';
                                tr += `<td class="px-2 py-2 text-center ${{row.color}} ${{alertBg}} cursor-pointer hover:bg-blue-100 dark:hover:bg-blue-900/50" onclick="showPopup('${{row.popupCategory}}', '${{abbr}}'${{filterArg}})">${{val}}${{suffix}}</td>`;
                            }} else {{
                                tr += `<td class="px-2 py-2 text-center ${{row.color}} ${{alertBg}}">${{val}}${{suffix}}</td>`;
                            }}
                        }});
                    }});
                    const totalVal = row.getTotal ? row.getTotal() : 0;
                    tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{row.color}} font-bold">${{totalVal}}</td>`;
                    tr += '</tr>';
                    bodyRows += tr;
                }});
                table.innerHTML = `
                    <thead class="bg-slate-50 dark:bg-slate-800/50">${{buildHeader()}}</thead>
                    <tbody class="divide-y divide-slate-100 dark:divide-slate-700">${{bodyRows}}</tbody>
                `;
            }}

            // Traffic (MTD) rows
            const mtdRows = [
                {{ label: 'Total Prospects' + tip('Month to date'), color: 'text-blue-600 dark:text-blue-400 font-semibold',
                   getValue: abbr => (trafficData[abbr]?.total || 0), getTotal: () => totals.total }},
                {{ label: 'Daily AVG' + tip('Average prospects per day since 1st of current month'), color: 'text-slate-600 dark:text-slate-300',
                   getValue: abbr => (trafficData[abbr]?.daily_avg || 0), getTotal: () => totals.dailyAvg.toFixed(1) }},
                {{ label: 'Total Signed Leases' + tip('New leases signed this month (MTD). Excludes renewals.'), color: 'text-emerald-600 dark:text-emerald-400 font-semibold',
                   getValue: abbr => (leasesData[abbr]?.signed_leases_mtd || 0), getTotal: () => totals.signedLeasesMtd }},
                {{ label: 'Conversion Rate %' + tip('Total Signed Leases / Total Prospects'), color: 'text-purple-600 dark:text-purple-400',
                   getValue: abbr => {{
                       const p = trafficData[abbr]?.total || 0;
                       const s = leasesData[abbr]?.signed_leases_mtd || 0;
                       return p === 0 ? '0.0' : ((s / p) * 100).toFixed(1);
                   }}, isPercent: true,
                   getTotal: () => (totals.total > 0 ? ((totals.signedLeasesMtd / totals.total) * 100).toFixed(1) : '0.0') + '%' }},
                {{ isDivider: true }},
                {{ label: 'Applicants MTD' + tip('Applications submitted this month (any outcome). Excludes Pending Transfer.'), color: 'text-indigo-600 dark:text-indigo-400 font-semibold',
                   getValue: abbr => (leasesData[abbr]?.applicants_mtd || 0), popupCategory: 'applicantsMtd', getTotal: () => totals.applicantsMtd }},
                {{ label: 'Approved' + tip('Of MTD applications: currently approved or signed'), color: 'text-cyan-600 dark:text-cyan-400',
                   getValue: abbr => (leasesData[abbr]?.applicants_mtd_approved || 0), popupCategory: 'applicantsMtd', popupFilter: 'Approved', getTotal: () => totals.applicantsMtdApproved }},
                {{ label: 'Denied' + tip('Of MTD applications: currently denied'), color: 'text-rose-600 dark:text-rose-400',
                   getValue: abbr => (leasesData[abbr]?.applicants_mtd_denied || 0), popupCategory: 'applicantsMtd', popupFilter: 'Denied', getTotal: () => totals.applicantsMtdDenied }},
                {{ label: 'Cancelled' + tip('Of MTD applications: currently cancelled'), color: 'text-slate-500 dark:text-slate-400',
                   getValue: abbr => (leasesData[abbr]?.applicants_mtd_cancelled || 0), popupCategory: 'applicantsMtd', popupFilter: 'Cancelled', getTotal: () => totals.applicantsMtdCancelled }},
                {{ label: 'Still Active' + tip('Of MTD applications: still pending (no outcome yet)'), color: 'text-amber-600 dark:text-amber-400',
                   getValue: abbr => (leasesData[abbr]?.applicants_mtd_active || 0), popupCategory: 'applicantsMtd', popupFilter: 'Still Active', getTotal: () => totals.applicantsMtdActive }},
            ];

            // Alert aggregator for Total Applicants cell: returns summary string (or null)
            function applicantAlertSummary(abbr) {{
                const l = leasesData[abbr] || {{}};
                const alerts = [];
                (l.pendingInRealUnitList || []).forEach(r => {{ if (r.hasAlert) alerts.push(r); }});
                (l.approvedPaidInHoldingList || []).forEach(r => {{ if (r.hasAlert) alerts.push(r); }});
                if (alerts.length === 0) return null;
                if (alerts.length === 1) return alerts[0].alertMsg || 'Needs attention';
                return `${{alerts.length}} applicant alerts`;
            }}

            // Traffic (Snapshot) rows
            const snapshotRows = [
                {{ label: 'Total Applicants' + tip('Currently active applicants'), color: 'text-indigo-600 dark:text-indigo-400 font-semibold',
                   getValue: abbr => (leasesData[abbr]?.total_applicants || 0), popupCategory: 'applicants',
                   getAlert: applicantAlertSummary,
                   getTotal: () => totals.totalApplicants }},
                {{ label: 'Pending Approval', color: 'text-amber-600 dark:text-amber-400',
                   getValue: abbr => (leasesData[abbr]?.pending_approval || 0), getTotal: () => totals.pendingApproval }},
                {{ label: 'Approved', color: 'text-cyan-600 dark:text-cyan-400',
                   getValue: abbr => (leasesData[abbr]?.approved || 0), getTotal: () => totals.approved }},
                {{ label: 'Signed Lease', color: 'text-emerald-600 dark:text-emerald-400',
                   getValue: abbr => (leasesData[abbr]?.signed_lease || 0), getTotal: () => totals.signedLease }},
            ];

            renderSection('traffic-mtd-matrix-table', mtdRows);
            renderSection('traffic-snapshot-matrix-table', snapshotRows);
        }}

        function renderExpiringTable() {{
            const table = document.getElementById('expiring-matrix-table');

            // Get all properties in order by region
            const regions = Object.keys(propertyGroups);
            const allProps = [];
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    allProps.push({{ abbr, region }});
                }});
            }});

            // Get month labels from first property with data
            let monthLabels = ['Month 1', 'Month 2', 'Month 3', 'Month 4'];
            for (const abbr in leasesData) {{
                if (leasesData[abbr]?.expiring?.month1_label) {{
                    monthLabels = [
                        leasesData[abbr].expiring.month1_label,
                        leasesData[abbr].expiring.month2_label,
                        leasesData[abbr].expiring.month3_label,
                        leasesData[abbr].expiring.month4_label
                    ];
                    break;
                }}
            }}

            // Region colors
            const regionColors = {{
                'Tampa': 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
                'Orlando': 'bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
                'Jacksonville': 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300',
                'Ft. Lauderdale': 'bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'
            }};

            // Build header with region grouping
            let headerRow1 = '<tr class="border-b border-slate-200 dark:border-slate-700"><th class="px-3 py-2 text-left font-bold text-slate-700 dark:text-slate-200 bg-slate-100 dark:bg-slate-700/50 sticky left-0 z-10 w-[140px]"></th>';
            let headerRow2 = '<tr class="border-b-2 border-slate-300 dark:border-slate-600"><th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/50 sticky left-0 z-10 w-[140px]">Status / Month</th>';

            regions.forEach(region => {{
                const propsInRegion = propertyGroups[region];
                const colspan = propsInRegion.length;
                if (colspan > 0) {{
                    headerRow1 += `<th colspan="${{colspan}}" class="px-2 py-2 text-center font-bold text-xs uppercase tracking-wide ${{regionColors[region] || 'bg-slate-100 dark:bg-slate-700'}}">${{region}}</th>`;
                    propsInRegion.forEach(abbr => {{
                        headerRow2 += `<th class="px-2 py-2 text-center w-[65px]">
                            <div class="font-bold text-slate-900 dark:text-white text-xs">${{abbr}}</div>
                        </th>`;
                    }});
                }}
            }});

            headerRow1 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">TOTAL</th></tr>';
            headerRow2 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">Portfolio</th></tr>';

            // Status definitions - matches Python logic
            const statuses = [
                {{ key: 'Current', color: 'text-slate-600 dark:text-slate-400' }},
                {{ key: 'Pending Renewal', color: 'text-amber-600 dark:text-amber-400' }},
                {{ key: 'Renewed', color: 'text-emerald-600 dark:text-emerald-400' }},
                {{ key: 'NTV/Formers', color: 'text-orange-600 dark:text-orange-400', tooltip: 'NTV + Evicted + Formers with move-out in past 3 months' }},
                {{ key: 'Under Eviction', color: 'text-red-600 dark:text-red-400' }}
            ];

            // Build rows for each month (current + next 3 = 4 months)
            let bodyRows = '';
            const months = ['month1', 'month2', 'month3', 'month4'];

            months.forEach((monthKey, monthIdx) => {{
                // Calculate renewal % for this month (Renewed / Total excluding Pending Renewal)
                let monthRenewed = 0;
                let monthTotal = 0;
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const expData = leasesData[abbr]?.expiring?.[monthKey] || {{}};
                        monthRenewed += expData['Renewed'] || 0;
                        // Total = all except Pending Renewal
                        monthTotal += (expData['Current'] || 0) + (expData['Renewed'] || 0) + (expData['NTV/Formers'] || 0) + (expData['Under Eviction'] || 0);
                    }});
                }});
                const renewalPct = monthTotal > 0 ? (monthRenewed / monthTotal * 100).toFixed(1) : '0.0';
                const pctColor = parseFloat(renewalPct) >= 70 ? 'text-emerald-600 dark:text-emerald-400' :
                                 parseFloat(renewalPct) >= 50 ? 'text-amber-600 dark:text-amber-400' :
                                 'text-red-600 dark:text-red-400';

                // Month header row (collapsible)
                bodyRows += `<tr class="bg-slate-100 dark:bg-slate-700/50 cursor-pointer hover:bg-slate-200 dark:hover:bg-slate-600" onclick="toggleMonth('${{monthKey}}')">
                    <td class="px-3 py-2 font-bold text-slate-700 dark:text-slate-200 sticky left-0 bg-slate-100 dark:bg-slate-700/50">
                        <span class="inline-flex items-center gap-2">
                            <svg id="chevron-${{monthKey}}" class="w-4 h-4 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                            </svg>
                            ${{monthLabels[monthIdx]}}
                        </span>
                    </td>`;

                // Show per-property renewal % in header
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const expData = leasesData[abbr]?.expiring?.[monthKey] || {{}};
                        const propRenewed = expData['Renewed'] || 0;
                        const propTotal = (expData['Current'] || 0) + (expData['Renewed'] || 0) + (expData['NTV/Formers'] || 0) + (expData['Under Eviction'] || 0);
                        const propPct = propTotal > 0 ? (propRenewed / propTotal * 100).toFixed(0) : '-';
                        const propColor = propTotal === 0 ? 'text-slate-400' :
                                         parseFloat(propPct) >= 70 ? 'text-emerald-600 dark:text-emerald-400' :
                                         parseFloat(propPct) >= 50 ? 'text-amber-600 dark:text-amber-400' :
                                         'text-red-600 dark:text-red-400';
                        const monthRows = leasesData[abbr]?.expiring?.expiringLists?.[monthKey] || [];
                        const hasMonthAlert = monthRows.some(r => r.hasAlert);
                        const alertBg = hasMonthAlert ? 'bg-red-100 dark:bg-blue-900/40' : '';
                        if (propTotal > 0) {{
                            bodyRows += `<td class="px-2 py-2 text-center font-semibold ${{propColor}} ${{alertBg}} cursor-pointer hover:bg-blue-100 dark:hover:bg-blue-900/50" onclick="event.stopPropagation(); showPopup('expiring', '${{abbr}}', '${{monthKey}}')">${{propPct}}%</td>`;
                        }} else {{
                            bodyRows += `<td class="px-2 py-2 text-center font-semibold ${{propColor}} ${{alertBg}}">-</td>`;
                        }}
                    }});
                }});

                bodyRows += `<td class="px-3 py-2 text-center bg-slate-200 dark:bg-slate-600 font-bold ${{pctColor}}">${{renewalPct}}%</td></tr>`;

                // Total row FIRST (collapsible content)
                let tr = `<tr class="month-content-${{monthKey}} bg-slate-50 dark:bg-slate-800/50" style="display: none;">`;
                tr += `<td class="px-3 py-2 pl-8 font-bold text-slate-700 dark:text-slate-200 sticky left-0 bg-slate-50 dark:bg-slate-800/50 border-r border-slate-200 dark:border-slate-700">Total</td>`;

                let grandTotal = 0;
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const expData = leasesData[abbr]?.expiring?.[monthKey] || {{}};
                        let propTotal = 0;
                        statuses.forEach(s => {{ propTotal += expData[s.key] || 0; }});
                        grandTotal += propTotal;
                        tr += `<td class="px-2 py-2 text-center font-bold text-slate-700 dark:text-slate-200">${{propTotal}}</td>`;
                    }});
                }});

                tr += `<td class="px-3 py-2 text-center bg-slate-200 dark:bg-slate-600 font-bold text-slate-900 dark:text-white">${{grandTotal}}</td>`;
                tr += '</tr>';
                bodyRows += tr;

                // Status breakdown rows (collapsible content)
                statuses.forEach((status, idx) => {{
                    const isAlt = idx % 2 === 1;
                    const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                    let tr = `<tr class="month-content-${{monthKey}} ${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20 border-b border-slate-200 dark:border-slate-700" style="display: none;">`;
                    const tooltipHtml = status.tooltip ? `<span class="tip" data-tip="${{status.tooltip}}">?</span>` : '';
                    tr += `<td class="px-3 py-2 pl-8 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700">${{status.key}}${{tooltipHtml}}</td>`;

                    let rowTotal = 0;
                    regions.forEach(region => {{
                        propertyGroups[region].forEach(abbr => {{
                            const expData = leasesData[abbr]?.expiring?.[monthKey] || {{}};
                            const val = expData[status.key] || 0;
                            rowTotal += val;
                            tr += `<td class="px-2 py-2 text-center ${{val > 0 ? status.color : 'text-slate-400 dark:text-slate-500'}}">${{val}}</td>`;
                        }});
                    }});

                    tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{status.color}} font-bold">${{rowTotal}}</td>`;
                    tr += '</tr>';
                    bodyRows += tr;
                }});
            }});

            table.innerHTML = `
                <thead class="bg-slate-50 dark:bg-slate-800/50">
                    ${{headerRow1}}
                    ${{headerRow2}}
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-700">
                    ${{bodyRows}}
                </tbody>
            `;
        }}

        function renderRenovationTable() {{
            const table = document.getElementById('renovation-matrix-table');

            // Get all properties in order by region
            const regions = Object.keys(propertyGroups);
            const allProps = [];
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    if (leasesData[abbr]?.renovation) {{
                        allProps.push({{ abbr, region }});
                    }}
                }});
            }});

            // Region colors (matching Portfolio table)
            const regionColors = {{
                'Tampa': 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
                'Orlando': 'bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
                'Jacksonville': 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300',
                'Ft. Lauderdale': 'bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'
            }};

            // Build header row 1 (regions) - matching Portfolio table
            let headerRow1 = '<tr class="border-b border-slate-200 dark:border-slate-700"><th class="px-3 py-2 text-left font-bold text-slate-700 dark:text-slate-200 bg-slate-100 dark:bg-slate-700/50 sticky left-0 z-10 w-[140px]"></th>';
            regions.forEach(region => {{
                const propsInRegion = propertyGroups[region].filter(a => leasesData[a]?.renovation);
                const colspan = propsInRegion.length;
                if (colspan > 0) {{
                    headerRow1 += `<th colspan="${{colspan}}" class="px-2 py-2 text-center font-bold text-xs uppercase tracking-wide ${{regionColors[region] || 'bg-slate-100 dark:bg-slate-700'}}">${{region}}</th>`;
                }}
            }});
            headerRow1 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">TOTAL</th></tr>';

            // Build header row 2 (properties) - matching Portfolio table
            let headerRow2 = '<tr class="border-b-2 border-slate-300 dark:border-slate-600"><th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/50 sticky left-0 z-10 w-[140px]">Status</th>';
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    if (leasesData[abbr]?.renovation) {{
                        headerRow2 += `<th class="px-2 py-2 text-center w-[65px]">
                            <div class="font-bold text-slate-900 dark:text-white text-xs">${{abbr}}</div>
                        </th>`;
                    }}
                }});
            }});
            headerRow2 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">Portfolio</th></tr>';

            // Define renovation statuses with colors
            const statuses = [
                {{ key: 'total', label: 'Total Units', color: 'text-slate-900 dark:text-white font-bold', isTotal: true }},
                {{ key: 'renovated', label: 'Renovated', color: 'text-emerald-600 dark:text-emerald-400' }},
                {{ key: 'partial', label: 'Partial Renovated', color: 'text-amber-600 dark:text-amber-400' }},
                {{ key: 'classic', label: 'Classic', color: 'text-slate-600 dark:text-slate-400' }}
            ];

            // Calculate grand total for percentage
            let grandTotal = 0;
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    const renoData = leasesData[abbr]?.renovation || {{}};
                    grandTotal += renoData.total || 0;
                }});
            }});

            // Build body rows
            let bodyRows = '';

            statuses.forEach((status, idx) => {{
                const isAlt = idx % 2 === 1;
                const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                let tr = `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                tr += `<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700">${{status.label}}</td>`;

                let rowTotal = 0;
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const renoData = leasesData[abbr]?.renovation || {{}};
                        const val = status.isTotal ? (renoData.total || 0) : (renoData[status.key] || 0);
                        const pctVal = status.isTotal ? '' : (renoData[status.key + '_pct'] || 0);
                        rowTotal += val;

                        if (status.isTotal) {{
                            tr += `<td class="px-2 py-2 text-center ${{status.color}}">${{val}}</td>`;
                        }} else {{
                            tr += `<td class="px-2 py-2 text-center ${{val > 0 ? status.color : 'text-slate-400 dark:text-slate-500'}}">
                                <span class="font-semibold">${{val}}</span>
                                <span class="text-[10px] opacity-70 ml-0.5">(${{pctVal}}%)</span>
                            </td>`;
                        }}
                    }});
                }});

                // Total column
                if (status.isTotal) {{
                    tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{status.color}}">${{rowTotal}}</td>`;
                }} else {{
                    const rowPct = grandTotal > 0 ? (rowTotal / grandTotal * 100).toFixed(1) : 0;
                    tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{status.color}} font-bold">
                        <span>${{rowTotal}}</span>
                        <span class="text-[10px] opacity-70 ml-0.5">(${{rowPct}}%)</span>
                    </td>`;
                }}
                tr += '</tr>';
                bodyRows += tr;
            }});

            // Add thick divider before Work Orders section
            const colCount = allProps.length + 2; // +2 for label and total columns
            bodyRows += `<tr class="h-2 bg-slate-200 dark:bg-slate-600"><td colspan="${{colCount}}"></td></tr>`;

            // Add Work Orders rows
            const woMetrics = [
                {{ key: 'open_total', label: 'Open WOs', color: 'text-blue-600 dark:text-blue-400' }},
                {{ key: 'new_24h', label: 'New (24h)', color: 'text-amber-600 dark:text-amber-400' }}
            ];

            woMetrics.forEach((metric, idx) => {{
                const isAlt = idx % 2 === 1;
                const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                let tr = `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                tr += `<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700">${{metric.label}}</td>`;

                let rowTotal = 0;
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const woData = leasesData[abbr]?.work_orders || {{}};
                        const val = woData[metric.key] || 0;
                        rowTotal += val;

                        const valColor = val > 0 ? metric.color : 'text-slate-400 dark:text-slate-500';
                        tr += `<td class="px-2 py-2 text-center font-semibold ${{valColor}}">${{val}}</td>`;
                    }});
                }});

                // Total column
                tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{metric.color}} font-bold">${{rowTotal}}</td>`;
                tr += '</tr>';
                bodyRows += tr;
            }});

            table.innerHTML = `
                <thead class="bg-slate-50 dark:bg-slate-800/50">
                    ${{headerRow1}}
                    ${{headerRow2}}
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-700">
                    ${{bodyRows}}
                </tbody>
            `;
        }}

        function renderFinancialTable() {{
            const table = document.getElementById('financial-matrix-table');
            if (!financialData || !financialData.income) {{
                table.innerHTML = '<tr><td class="p-4 text-slate-500 dark:text-slate-400">No financial data available</td></tr>';
                return;
            }}

            const regions = Object.keys(propertyGroups);
            const allProps = [];
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    allProps.push({{ abbr, region }});
                }});
            }});

            const regionColors = {{
                'Tampa': 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
                'Orlando': 'bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
                'Jacksonville': 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300',
                'Ft. Lauderdale': 'bg-rose-100 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300'
            }};

            // Build header row 1 (regions) - matching Portfolio table
            let headerRow1 = '<tr class="border-b border-slate-200 dark:border-slate-700"><th class="px-3 py-2 text-left font-bold text-slate-700 dark:text-slate-200 bg-slate-100 dark:bg-slate-700/50 sticky left-0 z-10 w-[140px]"></th>';
            regions.forEach(region => {{
                const colspan = propertyGroups[region].length;
                headerRow1 += `<th colspan="${{colspan}}" class="px-2 py-2 text-center font-bold text-xs uppercase tracking-wide ${{regionColors[region]}}">${{region}}</th>`;
            }});
            headerRow1 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">TOTAL</th></tr>';

            // Build header row 2 (properties) - matching Portfolio table
            let headerRow2 = '<tr class="border-b-2 border-slate-300 dark:border-slate-600"><th class="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/50 sticky left-0 z-10 w-[140px]">Status</th>';
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    headerRow2 += `<th class="px-2 py-2 text-center w-[65px]">
                            <div class="font-bold text-slate-900 dark:text-white text-xs">${{abbr}}</div>
                        </th>`;
                }});
            }});
            headerRow2 += '<th class="px-3 py-2 text-center font-bold bg-slate-200 dark:bg-slate-600 text-slate-800 dark:text-white w-[70px]">Portfolio</th></tr>';

            // Format currency - abbreviated for display, full for tooltip
            const fmtShort = (v) => v >= 1000000 ? '$' + (v/1000000).toFixed(1) + 'M' : v >= 1000 ? '$' + Math.round(v/1000) + 'K' : '$' + Math.round(v);
            const fmtFull = (v) => '$' + v.toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});

            // Income section rows
            const incomeRows = [
                {{ key: 'charge', label: 'Total Income', color: 'text-emerald-600 dark:text-emerald-400' }},
                {{ key: 'credit', label: 'Total Credit', color: 'text-red-600 dark:text-red-400' }},
                {{ key: 'net', label: 'Net', color: 'text-blue-600 dark:text-blue-400 font-bold' }},
                {{ key: 'receivable', label: 'Receivable MTD', color: 'text-purple-600 dark:text-purple-400 font-semibold', tip: "This month's charges not yet paid" }}
            ];

            let bodyRows = '';
            incomeRows.forEach((row, idx) => {{
                const isAlt = idx % 2 === 1;
                const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                let tr = `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                const tipHtml = row.tip ? `<span class="tip" data-tip="${{row.tip}}">?</span>` : '';
                const cellZ = row.tip ? ' z-20' : '';
                tr += `<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700 w-[140px] whitespace-nowrap${{cellZ}}">${{row.label}}${{tipHtml}}</td>`;

                let rowTotal = 0;
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const incData = financialData.income[abbr] || {{ charge: 0, credit: 0 }};
                        let val = 0;
                        if (row.key === 'charge') val = incData.charge || 0;
                        else if (row.key === 'credit') val = incData.credit || 0;
                        else if (row.key === 'net') val = (incData.charge || 0) - (incData.credit || 0);
                        else if (row.key === 'receivable') val = incData.receivable_mtd || 0;
                        rowTotal += val;
                        const tooltip = `data-count="${{fmtFull(val)}}"`;
                        tr += `<td class="px-2 py-2 text-center ${{row.color}} fin-cell" ${{tooltip}}>${{fmtShort(val)}}</td>`;
                    }});
                }});
                const totalTooltip = `data-count="${{fmtFull(rowTotal)}}"`;
                tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{row.color}} fin-cell" ${{totalTooltip}}>${{fmtShort(rowTotal)}}</td>`;
                tr += '</tr>';
                bodyRows += tr;
            }});

            // Del %: Receivable MTD as a percentage of Net (charge - credit)
            let delPctRow = '<tr class="hover:bg-blue-50/50 dark:hover:bg-blue-900/20">';
            delPctRow += '<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700 w-[140px]">Del %</td>';
            let dpRec = 0, dpNet = 0;
            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    const incData = financialData.income[abbr] || {{ charge: 0, credit: 0 }};
                    const net = (incData.charge || 0) - (incData.credit || 0);
                    const rec = incData.receivable_mtd || 0;
                    dpRec += rec; dpNet += net;
                    const pct = net > 0 ? (rec / net * 100) : 0;
                    const c = pct > 10 ? 'text-red-600 dark:text-red-400' : pct > 5 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400';
                    delPctRow += `<td class="px-2 py-2 text-center font-semibold ${{c}}">${{net > 0 ? pct.toFixed(1) + '%' : '—'}}</td>`;
                }});
            }});
            const dpPct = dpNet > 0 ? (dpRec / dpNet * 100) : 0;
            const dpc = dpPct > 10 ? 'text-red-600 dark:text-red-400' : dpPct > 5 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400';
            delPctRow += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 font-bold ${{dpc}}">${{dpNet > 0 ? dpPct.toFixed(1) + '%' : '—'}}</td>`;
            delPctRow += '</tr>';
            bodyRows += delPctRow;

            // Add divider before delinquency
            const colCount = allProps.length + 2;
            bodyRows += `<tr class="h-2 bg-slate-200 dark:bg-slate-600"><td colspan="${{colCount}}"></td></tr>`;

            // Delinquency section rows
            const delRows = [
                {{ key: 'current', label: 'Current', color: 'text-amber-600 dark:text-amber-400' }},
                {{ key: 'eviction', label: 'Under Eviction', color: 'text-red-600 dark:text-red-400' }},
                {{ key: 'formers', label: 'Formers', color: 'text-slate-600 dark:text-slate-400' }},
                {{ key: 'delTotal', label: 'Total', color: 'text-red-700 dark:text-red-300 font-bold' }}
            ];

            delRows.forEach((row, idx) => {{
                const isAlt = idx % 2 === 1;
                const rowBg = isAlt ? 'bg-slate-50/50 dark:bg-slate-800/30' : '';
                let tr = `<tr class="${{rowBg}} hover:bg-blue-50/50 dark:hover:bg-blue-900/20">`;
                tr += `<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 ${{rowBg}} bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700 w-[140px]">${{row.label}}</td>`;

                let rowTotal = 0;
                let rowCount = 0;
                regions.forEach(region => {{
                    propertyGroups[region].forEach(abbr => {{
                        const delData = financialData.delinquency[abbr] || {{}};
                        let val = 0, cnt = 0;
                        if (row.key === 'delTotal') {{
                            val = (delData.current?.amount || 0) + (delData.eviction?.amount || 0) + (delData.formers?.amount || 0);
                            cnt = (delData.current?.count || 0) + (delData.eviction?.count || 0) + (delData.formers?.count || 0);
                        }} else {{
                            val = delData[row.key]?.amount || 0;
                            cnt = delData[row.key]?.count || 0;
                        }}
                        rowTotal += val;
                        rowCount += cnt;
                        const tooltip = `data-count="${{fmtFull(val)}}${{cnt > 0 ? ' (' + cnt + ')' : ''}}"`;
                        tr += `<td class="px-2 py-2 text-center ${{row.color}} fin-cell" ${{tooltip}}>${{fmtShort(val)}}</td>`;
                    }});
                }});
                const totalTooltip = `data-count="${{fmtFull(rowTotal)}}${{rowCount > 0 ? ' (' + rowCount + ')' : ''}}"`;
                tr += `<td class="px-3 py-2 text-center bg-slate-100 dark:bg-slate-700/50 ${{row.color}} fin-cell" ${{totalTooltip}}>${{fmtShort(rowTotal)}}</td>`;
                tr += '</tr>';
                bodyRows += tr;
            }});

            // Add % of Net Income row
            let pctRow = '<tr class="bg-slate-100 dark:bg-slate-700/30 border-t-2 border-slate-300 dark:border-slate-600">';
            pctRow += '<td class="px-3 py-2 font-medium text-slate-700 dark:text-slate-300 sticky left-0 bg-slate-100 dark:bg-slate-700/30 border-r border-slate-200 dark:border-slate-700 w-[140px]">% of Net Income</td>';

            let portfolioDelTotal = 0;
            let portfolioNetTotal = 0;

            regions.forEach(region => {{
                propertyGroups[region].forEach(abbr => {{
                    const incData = financialData.income[abbr] || {{ charge: 0, credit: 0 }};
                    const delData = financialData.delinquency[abbr] || {{}};
                    const netIncome = (incData.charge || 0) - (incData.credit || 0);
                    const totalDel = (delData.current?.amount || 0) + (delData.eviction?.amount || 0) + (delData.formers?.amount || 0);

                    portfolioDelTotal += totalDel;
                    portfolioNetTotal += netIncome;

                    const pct = netIncome > 0 ? (totalDel / netIncome * 100) : 0;
                    const pctColor = pct > 10 ? 'text-red-600 dark:text-red-400' : pct > 5 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400';
                    pctRow += `<td class="px-2 py-2 text-center font-semibold ${{pctColor}}">${{pct.toFixed(1)}}%</td>`;
                }});
            }});

            const portfolioPct = portfolioNetTotal > 0 ? (portfolioDelTotal / portfolioNetTotal * 100) : 0;
            const portfolioPctColor = portfolioPct > 10 ? 'text-red-600 dark:text-red-400' : portfolioPct > 5 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400';
            pctRow += `<td class="px-3 py-2 text-center bg-slate-200 dark:bg-slate-600 font-bold ${{portfolioPctColor}}">${{portfolioPct.toFixed(1)}}%</td>`;
            pctRow += '</tr>';
            // bodyRows += pctRow;  // HIDDEN: % of Net Income row temporarily disabled

            table.innerHTML = `
                <thead class="bg-slate-50 dark:bg-slate-800/50">
                    ${{headerRow1}}
                    ${{headerRow2}}
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-700">
                    ${{bodyRows}}
                </tbody>
            `;
        }}

        function updateDashboard() {{
            const prop = document.getElementById('propertyFilter').value;
            const data = propertiesData[prop];
            if (!data) return;

            const isAll = prop === 'ALL';
            const sb = data.status_breakdown;
            const total = data.total_units;

            // Show/hide sections based on view
            document.getElementById('portfolio-table-section').style.display = isAll ? 'block' : 'none';
            document.getElementById('traffic-mtd-section').style.display = isAll ? 'block' : 'none';
            document.getElementById('traffic-snapshot-section').style.display = isAll ? 'block' : 'none';
            document.getElementById('expiring-table-section').style.display = isAll ? 'block' : 'none';
            document.getElementById('renovation-table-section').style.display = isAll ? 'block' : 'none';
            document.getElementById('financial-table-section').style.display = isAll ? 'block' : 'none';
            document.getElementById('kpi-section').style.display = isAll ? 'none' : 'grid';
            document.getElementById('charts-section').style.display = isAll ? 'none' : 'grid';
            document.getElementById('unit-lists-section').style.display = isAll ? 'none' : 'grid';

            // Skip rendering individual property sections if viewing portfolio
            if (isAll) return;

            // KPI Cards - Occupancy = (Total - Vacant - VacantLeased) / Total
            const vacantCount = (sb['Vacant'] || 0) + (sb['Vacant Leased'] || 0);
            const occupancyPct = ((total - vacantCount) / total * 100).toFixed(2);
            const kpis = [
                {{ label: 'Total', value: total, color: 'slate' }},
                {{ label: 'Occupied', value: total - vacantCount, color: 'emerald', pct: occupancyPct }},
                {{ label: 'Vacant', value: sb['Vacant'] || 0, color: 'blue' }},
                {{ label: 'V-Leased', value: sb['Vacant Leased'] || 0, color: 'indigo' }},
                {{ label: 'NTV', value: sb['NTV'] || 0, color: 'amber' }},
                {{ label: 'NTV-L', value: sb['NTV Leased'] || 0, color: 'orange' }},
                {{ label: 'Eviction', value: sb['Under Eviction'] || 0, color: 'red' }}
            ];

            document.getElementById('kpi-section').innerHTML = kpis.map(k => `
                <div class="kpi-card bg-white dark:bg-slate-800 p-4 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                    <div class="flex justify-between items-baseline">
                        <h3 class="text-xs font-medium text-slate-500 dark:text-slate-400">${{k.label}}</h3>
                        ${{k.pct ? `<span class="text-[10px] font-semibold text-${{k.color}}-700 dark:text-${{k.color}}-300 bg-${{k.color}}-100 dark:bg-${{k.color}}-900/50 px-1.5 py-0.5 rounded-full">${{k.pct}}%</span>` : ''}}
                    </div>
                    <p class="text-2xl font-bold text-${{k.color}}-600 dark:text-${{k.color}}-400 mt-1">${{k.value.toLocaleString()}}</p>
                </div>
            `).join('');

            // Progress Bars
            const statuses = [
                {{ key: 'Occupied', color: 'emerald' }},
                {{ key: 'Vacant', color: 'blue' }},
                {{ key: 'Vacant Leased', color: 'indigo' }},
                {{ key: 'NTV', color: 'amber' }},
                {{ key: 'NTV Leased', color: 'orange' }},
                {{ key: 'Under Eviction', color: 'red' }}
            ];

            document.getElementById('progress-bars').innerHTML = statuses.map(s => {{
                const count = sb[s.key] || 0;
                const pct = (count / total * 100).toFixed(1);
                return `
                    <div>
                        <div class="flex justify-between text-xs mb-1">
                            <span class="font-medium text-slate-700 dark:text-slate-300">${{s.key}}</span>
                            <span class="text-${{s.color}}-600 dark:text-${{s.color}}-400 font-semibold">${{count.toLocaleString()}} (${{pct}}%)</span>
                        </div>
                        <div class="h-2.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                            <div class="progress-bar h-full bg-gradient-to-r from-${{s.color}}-500 to-${{s.color}}-400 rounded-full" style="width: ${{pct}}%"></div>
                        </div>
                    </div>
                `;
            }}).join('');

            // Unit Lists
            const lists = [
                {{ key: 'Vacant', color: 'blue', label: 'Vacant Units' }},
                {{ key: 'NTV', color: 'amber', label: 'NTV Units' }},
                {{ key: 'Vacant Leased', color: 'indigo', label: 'Vacant Leased' }}
            ];

            document.getElementById('unit-lists-section').innerHTML = lists.map(l => {{
                const units = data.unit_lists[l.key] || [];
                const displayUnits = isAll ? units.slice(0, 50) : units;
                const hasMore = units.length > 50;
                return `
                    <div class="bg-white dark:bg-slate-800 p-4 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                        <div class="flex justify-between items-center mb-3">
                            <h2 class="text-sm font-bold text-slate-800 dark:text-white">${{l.label}}</h2>
                            <span class="text-xs font-semibold text-${{l.color}}-600 dark:text-${{l.color}}-400 bg-${{l.color}}-100 dark:bg-${{l.color}}-900/50 px-2 py-0.5 rounded-full">${{units.length}}</span>
                        </div>
                        <div class="overflow-y-auto fancy-scroll" style="max-height: 150px;">
                            <div class="grid grid-cols-4 gap-1.5">
                                ${{displayUnits.map(u => `<div class="px-1.5 py-1 rounded-md text-center font-mono text-[10px] font-medium bg-${{l.color}}-100 dark:bg-${{l.color}}-900/50 text-${{l.color}}-700 dark:text-${{l.color}}-300 truncate">${{u}}</div>`).join('')}}
                            </div>
                            ${{hasMore ? `<div class="text-center text-xs text-slate-500 dark:text-slate-400 mt-2">+${{units.length - 50}} more</div>` : ''}}
                        </div>
                    </div>
                `;
            }}).join('');

            // Chart
            const colors = getColors();
            if (statusChart) statusChart.destroy();
            statusChart = new Chart(document.getElementById('statusChart'), {{
                type: 'doughnut',
                data: {{
                    labels: ['Occupied', 'Vacant', 'V-Leased', 'NTV', 'NTV-L', 'Eviction'],
                    datasets: [{{
                        data: [sb['Occupied'] || 0, sb['Vacant'] || 0, sb['Vacant Leased'] || 0, sb['NTV'] || 0, sb['NTV Leased'] || 0, sb['Under Eviction'] || 0],
                        backgroundColor: [colors.occupied, colors.vacant, colors.vacantLeased, colors.ntv, colors.ntvLeased, colors.eviction],
                        borderWidth: 0, hoverOffset: 8
                    }}]
                }},
                options: {{
                    responsive: true, maintainAspectRatio: false, cutout: '60%',
                    plugins: {{
                        legend: {{ position: 'bottom', labels: {{ color: colors.text, padding: 10, font: {{ size: 10 }}, usePointStyle: true }} }}
                    }}
                }}
            }});
        }}

        // Refresh state management using localStorage
        const REFRESH_KEY = 'sns_refresh_state';
        const REFRESH_TIMEOUT = 6 * 60 * 1000; // 6 minutes max

        function getRefreshState() {{
            try {{
                const state = localStorage.getItem(REFRESH_KEY);
                return state ? JSON.parse(state) : null;
            }} catch (e) {{
                return null;
            }}
        }}

        function setRefreshState(originalTimestamp) {{
            localStorage.setItem(REFRESH_KEY, JSON.stringify({{
                startedAt: Date.now(),
                originalTimestamp: originalTimestamp,
                attempts: 0
            }}));
        }}

        function clearRefreshState() {{
            localStorage.removeItem(REFRESH_KEY);
        }}

        function updateRefreshAttempts(attempts) {{
            const state = getRefreshState();
            if (state) {{
                state.attempts = attempts;
                localStorage.setItem(REFRESH_KEY, JSON.stringify(state));
            }}
        }}

        function showLoadingOverlay() {{
            const overlay = document.getElementById('refresh-overlay');
            const btn = document.getElementById('refresh-btn');
            const icon = document.getElementById('refresh-icon');
            const spinner = document.getElementById('refresh-spinner');

            overlay.classList.remove('hidden');
            overlay.classList.add('flex');
            btn.disabled = true;
            icon.classList.add('hidden');
            spinner.classList.remove('hidden');
        }}

        function hideLoadingOverlay() {{
            const overlay = document.getElementById('refresh-overlay');
            const btn = document.getElementById('refresh-btn');
            const icon = document.getElementById('refresh-icon');
            const spinner = document.getElementById('refresh-spinner');

            overlay.classList.add('hidden');
            overlay.classList.remove('flex');
            btn.disabled = false;
            icon.classList.remove('hidden');
            spinner.classList.add('hidden');
        }}

        function getCurrentTimestamp() {{
            return document.getElementById('refresh-timestamp')?.textContent?.trim() || '';
        }}

        // Poll for changes - runs until timestamp changes or timeout
        function startPolling(originalTimestamp, initialAttempts = 0) {{
            const statusEl = document.getElementById('refresh-status');
            const progressEl = document.getElementById('refresh-progress');
            const timeEl = document.getElementById('refresh-time');

            const maxAttempts = 24;  // 24 attempts * 15 seconds = 6 minutes max
            const pollInterval = 15000;  // 15 seconds
            let attempts = initialAttempts;

            const poll = async () => {{
                attempts++;
                updateRefreshAttempts(attempts);

                const progress = Math.min(10 + (attempts / maxAttempts) * 85, 95);
                progressEl.style.width = progress + '%';
                statusEl.textContent = `Waiting for new data... (${{attempts}}/${{maxAttempts}})`;
                timeEl.textContent = `Checking every 15 seconds`;

                try {{
                    // Fetch the page with aggressive cache-busting
                    const cacheBuster = Date.now() + '_' + Math.random();
                    const pageResponse = await fetch(window.location.pathname + '?nocache=' + cacheBuster, {{
                        cache: 'no-store',
                        headers: {{
                            'Cache-Control': 'no-cache, no-store, must-revalidate',
                            'Pragma': 'no-cache'
                        }}
                    }});
                    const html = await pageResponse.text();

                    // Check if timestamp has changed
                    const match = html.match(/id="refresh-timestamp"[^>]*>([^<]+)</);
                    const newTimestamp = match ? match[1].trim() : '';

                    console.log('Polling: original="' + originalTimestamp + '", new="' + newTimestamp + '"');

                    if (newTimestamp && newTimestamp !== originalTimestamp) {{
                        // Success! New data is available
                        clearRefreshState();
                        statusEl.textContent = 'New data loaded! Refreshing page...';
                        progressEl.style.width = '100%';
                        setTimeout(() => {{
                            window.location.reload(true);
                        }}, 800);
                        return;
                    }}
                }} catch (e) {{
                    console.log('Poll error:', e);
                    statusEl.textContent = `Checking... (network retry)`;
                }}

                // Check if we should continue
                const state = getRefreshState();
                if (!state) {{
                    // State was cleared (maybe by another tab) - stop polling
                    hideLoadingOverlay();
                    return;
                }}

                // Check timeout
                if (Date.now() - state.startedAt > REFRESH_TIMEOUT) {{
                    statusEl.textContent = 'Taking longer than expected...';
                    timeEl.innerHTML = '<button onclick="window.location.reload(true)" class="text-blue-500 hover:text-blue-600 underline font-medium">Click to reload page</button>';
                    progressEl.style.width = '100%';
                    return;
                }}

                if (attempts < maxAttempts) {{
                    setTimeout(poll, pollInterval);
                }} else {{
                    statusEl.textContent = 'Still waiting for update...';
                    timeEl.innerHTML = '<button onclick="window.location.reload(true)" class="text-blue-500 hover:text-blue-600 underline font-medium">Click to reload page</button>';
                    progressEl.style.width = '100%';
                }}
            }};

            // Start polling
            setTimeout(poll, pollInterval);
        }}

        // Refresh data function - triggers GitHub Actions via Vercel proxy
        async function refreshData() {{
            const statusEl = document.getElementById('refresh-status');
            const progressEl = document.getElementById('refresh-progress');
            const timeEl = document.getElementById('refresh-time');

            // Get current timestamp before refresh
            const originalTimestamp = getCurrentTimestamp();

            // Show loading overlay immediately
            showLoadingOverlay();
            statusEl.textContent = 'Triggering data refresh...';
            progressEl.style.width = '5%';
            timeEl.textContent = 'Starting...';

            try {{
                // Save state to localStorage so it persists across page loads
                setRefreshState(originalTimestamp);

                const response = await fetch('/api', {{
                    method: 'POST'
                }});

                let data;
                try {{
                    data = await response.json();
                }} catch (e) {{
                    throw new Error('Invalid response from server');
                }}

                if (!data.success) {{
                    throw new Error(data.error || 'Failed to trigger refresh');
                }}

                // API call successful - now wait for GitHub Actions to complete
                statusEl.textContent = 'Refresh triggered! Waiting for GitHub Actions...';
                progressEl.style.width = '10%';
                timeEl.textContent = 'This typically takes 2-3 minutes';

                // Start polling after a short delay
                setTimeout(() => startPolling(originalTimestamp, 0), 5000);

            }} catch (err) {{
                clearRefreshState();
                hideLoadingOverlay();
                alert('Could not trigger refresh: ' + err.message);
            }}
        }}

        // Check on page load if a refresh is in progress
        function checkPendingRefresh() {{
            const state = getRefreshState();
            if (!state) return;

            const currentTimestamp = getCurrentTimestamp();
            const elapsed = Date.now() - state.startedAt;

            // If timestamp has changed, refresh is complete
            if (currentTimestamp !== state.originalTimestamp) {{
                clearRefreshState();
                return;
            }}

            // If refresh started recently (within timeout), resume polling
            if (elapsed < REFRESH_TIMEOUT) {{
                console.log('Resuming refresh polling (elapsed: ' + Math.round(elapsed/1000) + 's)');
                showLoadingOverlay();

                const statusEl = document.getElementById('refresh-status');
                const progressEl = document.getElementById('refresh-progress');
                const timeEl = document.getElementById('refresh-time');

                statusEl.textContent = 'Refresh in progress...';
                progressEl.style.width = (10 + (elapsed / REFRESH_TIMEOUT) * 80) + '%';
                timeEl.textContent = 'Waiting for new data';

                // Resume polling
                startPolling(state.originalTimestamp, state.attempts || 0);
            }} else {{
                // Timeout exceeded - clear state
                clearRefreshState();
            }}
        }}

        document.addEventListener('DOMContentLoaded', function() {{
            renderPortfolioTable();
            renderTrafficTables();
            renderExpiringTable();
            renderRenovationTable();
            renderFinancialTable();
            updateDashboard();
            updateAlertsBadge();

            // Check if there's a pending refresh
            checkPendingRefresh();
        }});
    </script>
</body>
</html>'''
    return html, new_alert_state

def main():
    print("=" * 60)
    print("SNS Daily Report - Generating Dashboard")
    print("=" * 60)

    # Get all properties
    print("\n[1] Fetching properties...")
    props_data = make_request("Account/GetProperties")
    all_properties = props_data.get("Properties", []) if props_data else []

    # Filter to target properties (preserving order from TARGET_PROPERTIES - smallest first)
    props_by_abbr = {p["Abbreviation"]: p for p in all_properties if p.get("Abbreviation") in TARGET_PROPERTIES}
    target_props = {abbr: props_by_abbr[abbr] for abbr in TARGET_PROPERTIES if abbr in props_by_abbr}
    print(f"    Found {len(target_props)} target properties")

    # Get data for each property (processing smaller properties first to warm up API)
    properties_data = {}
    leases_data = {}
    for abbr, prop in target_props.items():
        print(f"\n[2] Processing {abbr} - {prop.get('Name')}...")
        property_id = prop["PropertyID"]

        # OPTIMIZED: Fetch leases FIRST (needed for eviction/former status)
        all_leases = fetch_leases(property_id)

        # Fetch Units/Availability (replaces Legacy GetUnitStatus + Partners /Units)
        units_availability = fetch_units_availability(property_id)

        # Get property data (unit status, evictions) - uses Units/Availability + Leases
        data = get_property_data(property_id, prop.get("Name", abbr), units_availability, all_leases)
        data["name"] = prop.get("Name", abbr)
        data["property_id"] = property_id
        properties_data[abbr] = data
        print(f"    Units: {data['total_units']}, Occupancy: {data['occupancy_rate']}%")

        # Get pending lease data (using pre-fetched leases + units_availability for Holding-Unit detection)
        lease_data = get_pending_leases_data(property_id, leases=all_leases, units_availability=units_availability)
        leases_data[abbr] = lease_data
        print(f"    Applicants: {lease_data['total_applicants']} (Pending: {lease_data['pending_approval']}, Approved: {lease_data['approved']}, Signed: {lease_data['signed_lease']}, MTD New: {lease_data['signed_leases_mtd']})")

        # Get expiring leases data (using pre-fetched leases)
        expiring_data = get_expiring_leases_data(property_id, leases=all_leases, units_availability=units_availability)
        leases_data[abbr]["expiring"] = expiring_data
        exp_total = sum(expiring_data.get("total", {}).values())
        print(f"    Expiring (4mo): {exp_total}")

        # Get future move-outs (Future MI is calculated from Vacant/NTV Leased in HTML generation)
        future_moves = get_future_moves_data(property_id, leases=all_leases, units_availability=units_availability)
        leases_data[abbr]["future_moves"] = future_moves
        # Note: Future MI count is calculated in generate_html() from Vacant Leased + NTV Leased with current month move-in
        print(f"    Future MO ({future_moves['month_label']}): {future_moves['future_mo']}")

        # Get renovation status data (using Units/Availability)
        renovation_data = get_units_renovation_data(property_id, units_availability=units_availability)
        leases_data[abbr]["renovation"] = renovation_data
        print(f"    Renovation: {renovation_data['renovated']} Reno, {renovation_data['partial']} Partial, {renovation_data['classic']} Classic")

        # Get work orders data
        work_orders_data = get_work_orders_data(property_id)
        leases_data[abbr]["work_orders"] = work_orders_data
        print(f"    Work Orders: {work_orders_data['open_total']} Open, {work_orders_data['new_24h']} New (24h)")

        # Get off-market count (using Units/Availability)
        eviction_units = set(data.get("eviction_units", []))
        off_market_count = get_off_market_count(
            property_id,
            eviction_units=eviction_units,
            units_availability=units_availability,
            debug=(abbr == "EAST")
        )
        properties_data[abbr]["off_market"] = off_market_count
        print(f"    Off Market: {off_market_count}")

        # Add delay between property requests to avoid rate limiting
        time.sleep(PROPERTY_REQUEST_DELAY)

    # Fetch traffic data
    print("\n[3] Fetching traffic data...")
    traffic_data = get_traffic_data()
    if traffic_data:
        print(f"    Traffic data loaded for {len(traffic_data)} properties")
    else:
        print("    Warning: No traffic data available")

    # Hook for properties whose traffic comes from the ResMan Prospects API instead of the CRM sheet
    for abbr in RESMAN_TRAFFIC_PROPERTIES:
        pid = properties_data.get(abbr, {}).get("property_id")
        if pid:
            traffic_data[abbr] = get_resman_traffic_for_property(pid)
            print(f"    ResMan traffic for {abbr}: {traffic_data[abbr]}")

    # Fetch financial data
    print("\n[4] Fetching financial data...")
    financial_data = get_financial_data()
    if financial_data.get('income'):
        print(f"    Financial data loaded for {len(financial_data['income'])} properties")
    else:
        print("    Warning: No financial data available")

    # Receivable MTD (as-of-today owed balances) from ResMan, for ALL properties
    for abbr in TARGET_PROPERTIES:
        pid = properties_data.get(abbr, {}).get("property_id")
        if pid:
            income = financial_data.setdefault('income', {}).setdefault(abbr, {'charge': 0, 'credit': 0})
            income['receivable_mtd'] = get_receivables_mtd(pid)
            print(f"    Receivable MTD for {abbr}: ${income['receivable_mtd']:,.2f}")

    # Load previous alert state so firstSeen carries across refreshes
    from alert_state import load_alert_state, write_alert_state
    alert_state_path = os.path.join(os.path.dirname(__file__), "alerts_state.json")
    prev_alert_state = load_alert_state(alert_state_path)
    alert_today = datetime.now().date()

    # Report any auto-pushed Date Available PATCHes from this run
    mode_tag = " (DRY-RUN)" if AUTO_PUSH_DRY_RUN else ""
    if _auto_push_log:
        print(f"\n[4b] Auto-pushed NTV avDates{mode_tag}:")
        for line in _auto_push_log:
            print(line)
    else:
        print(f"\n[4b] No NTV avDate auto-pushes this run{mode_tag}.")

    # Generate HTML
    print("\n[5] Generating HTML dashboard...")
    html, new_alert_state = generate_html(
        properties_data, traffic_data, leases_data, financial_data,
        prev_alert_state=prev_alert_state, alert_today=alert_today,
    )

    # Save
    output_path = os.path.join(os.path.dirname(__file__), "dashboard_live.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n    Dashboard saved to: {output_path}")

    # Persist alert state (keys dropped if alert cleared this run)
    if AUTO_PUSH_DRY_RUN:
        print(f"    [DRY-RUN] Skipping alert_state.json write ({len(new_alert_state)} would be active)")
    else:
        try:
            write_alert_state(alert_state_path, new_alert_state, alert_today)
            print(f"    Alert state saved to: {alert_state_path} ({len(new_alert_state)} active)")
        except Exception as e:
            print(f"    Warning: Alert state save failed: {e}")

    # Renewal summary JSON (current month, per-property) - fetched by n8n end-of-month
    print("\n[5b] Writing renewal_summary.json...")
    try:
        summary_path = os.path.join(os.path.dirname(__file__), "renewal_summary.json")
        write_renewal_summary(leases_data, summary_path)
        print(f"    Renewal summary saved to: {summary_path}")
    except Exception as e:
        print(f"    Warning: Renewal summary export failed: {e}")

    # Excel snapshot (single file, overwritten each run) - fetched by n8n
    print("\n[6] Writing Excel snapshot...")
    try:
        from export_excel import export_dashboard_xlsx
        xlsx_path = os.path.join(os.path.dirname(__file__), "dashboard.xlsx")
        export_dashboard_xlsx(properties_data, traffic_data, leases_data,
                              financial_data, xlsx_path)
        print(f"    Excel saved to: {xlsx_path}")
    except Exception as e:
        print(f"    Warning: Excel export failed: {e}")

    print("\n" + "=" * 60)
    print("Done! Open dashboard_live.html in a browser to view.")
    print("=" * 60)

if __name__ == "__main__":
    main()
