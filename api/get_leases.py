"""
Vercel Serverless Function - Get Leases from ResMan
Endpoint: GET /api/get_leases?property=ORT&month=2026-08
"""
from http.server import BaseHTTPRequestHandler
import os
import json
import urllib.request
import urllib.error
import urllib.parse
import base64
import re
from datetime import datetime, timedelta


# =============================================================================
# CONFIGURATION
# =============================================================================

# ResMan API Credentials (from environment)
RESMAN_API_KEY = os.environ.get("RESMAN_API_KEY", "")
RESMAN_PARTNER_ID = os.environ.get("RESMAN_PARTNER_ID", "")
RESMAN_ACCOUNT_ID = os.environ.get("RESMAN_ACCOUNT_ID", "")
RESMAN_BASE_URL = "https://api.myresman.com"
RESMAN_PARTNERS_URL = "https://partners-api.myresman.com"

# Monday.com API (for Rent & Fees lookup)
MONDAY_API_KEY = os.environ.get("MONDAY_API_KEY", "")
MONDAY_API_URL = "https://api.monday.com/v2"
RENT_FEES_BOARD_ID = "5539779772"

# Target lease statuses
TARGET_STATUSES = ["current", "notice", "ntv", "eviction", "pending renewal"]

# Rent lookup tables by property
RENT_TABLES = {
    "ORT": {
        '500':  {'No': 949,  'PR': 999,  'Yes': 1049},
        '671':  {'No': 999,  'PR': 1049, 'Yes': 1099},
        '1000': {'No': 1099, 'PR': 1149, 'Yes': 1199},
        '1050': {'No': 1149, 'PR': 1199, 'Yes': 1249},
        '1100': {'No': 1229, 'PR': 1279, 'Yes': 1329},
        '1300': {'No': 1349, 'PR': 1399, 'Yes': 1449},
        '1350': {'No': 1199, 'PR': 1249, 'Yes': 1299},
        '1400': {'No': 1449, 'PR': 1499, 'Yes': 1549},
    }
}

# Item IDs for connect_boards column (by property and SQFT)
ITEM_IDS = {
    "ORT": {
        '500': 5560561812, '671': 5560561820, '1000': 5560561823,
        '1050': 5560561681, '1100': 5560561702, '1300': 5560561710,
        '1350': 5560561690, '1400': 5560561717,
    }
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def make_request(url, method="GET", data=None, headers=None, auth=None):
    """Make HTTP request and return response JSON"""
    if headers is None:
        headers = {}

    if auth:
        # Basic auth
        credentials = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers['Authorization'] = f'Basic {credentials}'

    if data is not None:
        data = json.dumps(data).encode('utf-8')
        if 'Content-Type' not in headers:
            headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        response = urllib.request.urlopen(req, timeout=60)
        return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}


def get_property_id(abbreviation):
    """Get PropertyID from ResMan by abbreviation"""
    url = f"{RESMAN_BASE_URL}/Account/GetProperties"
    payload = {
        "IntegrationPartnerID": RESMAN_PARTNER_ID,
        "APIKey": RESMAN_API_KEY,
        "AccountID": RESMAN_ACCOUNT_ID
    }
    result = make_request(url, method="POST", data=payload, headers={"Accept": "application/json"})

    if "error" in result:
        return None

    props = result.get("Properties", [])
    for p in props:
        if p.get("Abbreviation") == abbreviation:
            return p["PropertyID"]
    return None


def fetch_leases(property_id):
    """Fetch leases from ResMan Partners API"""
    since_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    params = urllib.parse.urlencode({
        "propertyId": property_id,
        "includeLeaseHistory": "true",
        "modifiedSince": since_date
    })
    url = f"{RESMAN_PARTNERS_URL}/Leasing/Leases?{params}"

    result = make_request(
        url,
        headers={"ResMan-Account-Id": RESMAN_ACCOUNT_ID, "Accept": "application/json"},
        auth=(RESMAN_PARTNER_ID, RESMAN_API_KEY)
    )

    if "error" in result:
        return []
    return result.get("leases", [])


def fetch_deposit(property_id, unit_id):
    """Fetch deposit from DepositLedger API"""
    params = urllib.parse.urlencode({
        "propertyId": property_id,
        "unitId": unit_id
    })
    url = f"{RESMAN_PARTNERS_URL}/Transactions/DepositLedger?{params}"

    result = make_request(
        url,
        headers={"ResMan-Account-Id": RESMAN_ACCOUNT_ID, "Accept": "application/json"},
        auth=(RESMAN_PARTNER_ID, RESMAN_API_KEY)
    )

    if "error" in result:
        return 0

    transactions = result.get("depositTransactions", [])
    return sum(t.get("amount", 0) for t in transactions)


def fetch_recurring_charges(property_id, unit_id):
    """Fetch recurring charges (Pet Rent, Parking, WD) from Ledger API"""
    # Get transactions from first of current month
    first_of_month = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    params = urllib.parse.urlencode({
        "propertyId": property_id,
        "unitId": unit_id,
        "transactionDateFrom": first_of_month
    })
    url = f"{RESMAN_PARTNERS_URL}/Transactions/Ledger?{params}"

    result = make_request(
        url,
        headers={"ResMan-Account-Id": RESMAN_ACCOUNT_ID, "Accept": "application/json"},
        auth=(RESMAN_PARTNER_ID, RESMAN_API_KEY)
    )

    if "error" in result:
        return 0, 0, 0

    transactions = result.get("transactions", [])

    pet_rent = 0
    parking = 0
    wd = 0

    for t in transactions:
        cat = (t.get("category") or "").lower()
        amount = t.get("amount", 0) or 0

        if "pet rent" in cat:  # Only "pet rent", not "pet fees"
            pet_rent += amount
        elif "parking" in cat:
            parking += amount
        elif "washer" in cat or "dryer" in cat or "w/d" in cat:
            wd += amount

    return pet_rent, parking, wd


def parse_unit_type(unit_type_name):
    """Parse unit type name to extract SQFT and renovation status"""
    sqft = None
    renovated = None

    # Extract SQFT
    match = re.search(r"(\d+)\s*sqft", unit_type_name, re.IGNORECASE)
    if match:
        sqft = int(match.group(1))

    # Determine renovation status
    unit_lower = unit_type_name.lower()
    if "partial" in unit_lower:
        renovated = "PR"
    elif "classic" in unit_lower:
        renovated = "No"
    elif "renovated" in unit_lower:
        renovated = "Yes"

    return sqft, renovated


def get_resident_name(people):
    """Get head of household name from people array"""
    for p in people:
        if p.get("isHeadOfHousehold") or p.get("isMainContact"):
            first = p.get("firstName", "") or ""
            last = p.get("lastName", "") or ""
            return f"{first} {last}".strip()
    # Fallback to first person
    if people:
        first = people[0].get("firstName", "") or ""
        last = people[0].get("lastName", "") or ""
        return f"{first} {last}".strip()
    return ""


def get_rent_from_history(history):
    """Get rent and other charges from most recent history entry"""
    if not history:
        return 0, 0
    current = history[-1]
    rent = current.get("rent", 0) or 0
    other_charges = current.get("otherCharges", 0) or 0
    return rent, other_charges


def get_status(lease):
    """Determine lease status"""
    status = lease.get("leaseStatus", "").lower()
    notice_date = lease.get("noticeToVacateDate")

    if "eviction" in status:
        return "Under Eviction"
    elif "notice" in status or notice_date:
        return "NTV"
    return "Current"


def lookup_rent_fees(property_abbr, sqft, renovated):
    """Lookup rent from property table"""
    if property_abbr not in RENT_TABLES:
        return 0

    table = RENT_TABLES[property_abbr]
    sqft_str = str(sqft) if sqft else ""

    if sqft_str in table and renovated in table[sqft_str]:
        return table[sqft_str][renovated]
    return 0


def get_item_id(property_abbr, sqft):
    """Get Monday.com item ID for connect_boards column"""
    if property_abbr not in ITEM_IDS:
        return None
    sqft_str = str(sqft) if sqft else ""
    return ITEM_IDS[property_abbr].get(sqft_str)


# =============================================================================
# MAIN HANDLER
# =============================================================================

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        try:
            # Parse query parameters
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            property_abbr = params.get("property", [""])[0].upper()
            target_month = params.get("month", [""])[0]  # Format: YYYY-MM

            if not property_abbr or not target_month:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing property or month parameter"}).encode())
                return

            # Step 1: Get property ID
            property_id = get_property_id(property_abbr)
            if not property_id:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Property '{property_abbr}' not found"}).encode())
                return

            # Step 2: Fetch all leases
            all_leases = fetch_leases(property_id)

            # Step 3: Filter by month and status
            filtered_leases = []
            for lease in all_leases:
                end_date = lease.get("leaseEndDate", "")
                status = lease.get("leaseStatus", "").lower()

                # Check if ends in target month
                if not end_date or not end_date.startswith(target_month):
                    continue

                # Check status
                status_match = any(t in status for t in TARGET_STATUSES)
                if status_match:
                    filtered_leases.append(lease)

            # Step 4: Transform leases with additional data
            result_leases = []
            for lease in filtered_leases:
                unit_id = lease.get("unitId")
                unit_number = lease.get("unitNumber", "")
                unit_type_name = lease.get("unitTypeName", "")

                # Parse unit type
                sqft, renovated = parse_unit_type(unit_type_name)
                if not renovated:
                    renovated = "No"

                # Get resident name
                people = lease.get("people", [])
                resident = get_resident_name(people)

                # Get rent from history
                history = lease.get("history", [])
                base_rent, other_charges = get_rent_from_history(history)
                current_total = base_rent + other_charges

                # Get dates
                move_in_date = lease.get("moveInDate", "")
                exp_date = lease.get("leaseEndDate", "")

                # Get status
                status = get_status(lease)

                # Fetch additional data from APIs
                deposit = fetch_deposit(property_id, unit_id)
                pet, parking, wd = fetch_recurring_charges(property_id, unit_id)

                # Lookup rent & fees
                rent_fees = lookup_rent_fees(property_abbr, sqft, renovated)

                # Get item ID for connect_boards
                item_id = get_item_id(property_abbr, sqft)

                result_leases.append({
                    "unit": unit_number,
                    "resident": resident,
                    "moveInDate": move_in_date,
                    "expDate": exp_date,
                    "baseRent": base_rent,
                    "currentTotal": current_total,
                    "sqft": sqft,
                    "renovated": renovated,
                    "rentFees": rent_fees,
                    "deposit": deposit,
                    "pet": pet,
                    "parking": parking,
                    "wd": wd,
                    "status": status,
                    "itemId": item_id,  # For connect_boards column
                    "unitId": unit_id,  # Keep for reference
                })

            # Sort by unit number
            result_leases.sort(key=lambda x: x["unit"])

            # Return response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "property": property_abbr,
                "month": target_month,
                "count": len(result_leases),
                "leases": result_leases
            }).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
