"""
Renewal Letters - Create Monday.com Items from ResMan Leases
Usage: python create_monday_items.py ORT 2026-08
"""
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import json
import sys
import re

# =============================================================================
# CONFIGURATION
# =============================================================================

# ResMan API Credentials
RESMAN_API_KEY = "43XzRFJkhvAJGLvR6GlJUpKy6nyc"
RESMAN_PARTNER_ID = "SNSaPi"
RESMAN_ACCOUNT_ID = "1550"
RESMAN_BASE_URL = "https://api.myresman.com"
RESMAN_PARTNERS_URL = "https://partners-api.myresman.com"

# Monday.com API
MONDAY_API_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjY0ODg5NDg5NiwiYWFpIjoxMSwidWlkIjo0MTgwNDM2NSwiaWFkIjoiMjAyNi0wNC0yMlQxNTowMDo1OC4wMDBaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6MzA4NjMwOSwicmduIjoidXNlMSJ9.ehN5YUi7BHwUuiB2FVJgFSDoj1RT8cYMl_YCBn7oC-Q"
MONDAY_API_URL = "https://api.monday.com/v2"

# Property to Board ID mapping
BOARD_IDS = {
    "ORT": 5562214499,
    "49THSTR": 5562194103,
    "MP": 5562197366,
    "TV": 5562203842,
    "MOC": 5562207113,
    "MUSO": 5562208618,
    "AP": 5562210480,
    "SJ": 5562211726,
    "ROP": 5562213066,
    "EAST": 5562216664,
    "TRA": 5945648005,
    "AN3": 5945648005,  # Shares board with TRA
}

# Target lease statuses
TARGET_STATUSES = ["current", "notice", "ntv", "eviction", "pending renewal"]

# Rent & Fees board (source of truth for rent lookups — fetched live at import time)
RENT_FEES_BOARD_ID = 5539779772
# Column IDs on the Rent & Fees board for each renovation label
RENT_FEES_COLUMN_IDS = {"No": "numbers", "PR": "numbers5", "Yes": "numbers0"}

# Item IDs for connect_boards column (by property and SQFT) - links to Rent & fees board
ITEM_IDS = {
    "ORT": {
        '500': 5560561812, '671': 5560561820, '1000': 5560561823,
        '1050': 5560561681, '1100': 5560561702, '1300': 5560561710,
        '1350': 5560561690, '1400': 5560561717,
    },
    "49THSTR": {
        '606': 5560561682, '707': 5560561695, '808': 5560561703,
        '818': 5560561714, '818V': 5560561719,
    },
    "MP": {
        '935': 5560561741,   # Jasmine
        '1220': 5560561753,  # Lily
    },
    "TV": {
        '910': 5560561762,
        '958': 5560561767,
    },
    "MOC": {
        '934': 5560561771,
        '1150': 5560561776,
        '1350': 5560561779,
        '1353': 5560561785,
    },
    "MUSO": {
        '853': 5560561798,
        '1044': 5560561807,
        '1144': 5560561813,
    },
    "AP": {
        '550': 5560561821,   # Oak
        '770': 5560561685,   # Maple
        '990': 5560561696,   # Sequoia
        '1200': 5560561706,  # Fern
    },
    "SJ": {
        '525': 5560561715,
        '604': 5560561720,
        '622': 5560561727,
        '726': 5560561739,
        '822': 5560561747,
        '825': 5560561760,
        '1235': 5560561764,
    },
    "ROP": {
        '700': 5560561770,
        '900': 5560561774,
        '1100': 5560561778,
        '1375': 5560561784,
    },
    "EAST": {
        '750': 5560561788,
        '850': 5560561795,
        '950': 5560561806,
    },
    "TRA": {
        '630': 5949002027,
        '750': 5949002038,
        '750C': 5949002049,
        '1000': 6845934056,
        '1018': 6845986945,
    }
}

# Property-specific column IDs (where they differ from default)
COLUMN_IDS = {
    "ORT": {
        "status_renewal": "status9",
        "rent_fees": "numbers88",
        "deposit": "numbers9",
    },
    "49THSTR": {
        "status_renewal": "status5",
        "rent_fees": "numbers97",
        "deposit": "numbers9",
    },
    "MP": {
        "status_renewal": "status0",
        "rent_fees": "numbers04",
        "deposit": "numbers5",
    },
    "TV": {
        "status_renewal": "status4",
        "rent_fees": "numbers07",
        "deposit": "numbers5",
    },
    "MOC": {
        "status_renewal": "status9",
        "rent_fees": "numbers030",
        "deposit": "numbers9",
    },
    "MUSO": {
        "status_renewal": "status8",
        "rent_fees": "numbers5",
        "deposit": "numbers7",
    },
    "AP": {
        "status_renewal": "status0",
        "rent_fees": "numbers1",
        "deposit": "numbers486",
    },
    "SJ": {
        "status_renewal": "status8",
        "rent_fees": "numbers1",
        "deposit": "numbers5",
    },
    "ROP": {
        "status_renewal": "status9",
        "rent_fees": "numbers7",
        "deposit": "numbers68",
    },
    "EAST": {
        "status_renewal": "status6",
        "rent_fees": "numbers71",
        "deposit": "numbers7",
    },
    "TRA": {
        "status_renewal": "status4",
        "rent_fees": "numbers3",
        "deposit": "numbers5",
    },
    "AN3": {  # Shares board with TRA
        "status_renewal": "status4",
        "rent_fees": "numbers3",
        "deposit": "numbers5",
    }
}

# Unit type parsing pattern by property
# "sqft" = look for "X sqft" pattern (e.g., "1000 sqft Classic")
# "prefix" = look for number at start (e.g., "707D Renovated")
# "flower" = look for flower name (e.g., "Jasmine Renovated", "Lily Classic")
UNIT_TYPE_PATTERN = {
    "ORT": "sqft",
    "49THSTR": "prefix",
    "MP": "flower",
    "TV": "prefix",
    "MOC": "sqft",
    "MUSO": "sqft",
    "AP": "tree",
    "SJ": "sqft",
    "ROP": "sqft",
    "EAST": "sqft",
    "TRA": "simple",
    "AN3": "simple",  # Shares board with TRA
}

# Flower name to SQFT mapping (for MP)
FLOWER_TO_SQFT = {
    "jasmine": ("935", 935),
    "lily": ("1220", 1220),
}

# Tree name to SQFT mapping (for AP)
TREE_TO_SQFT = {
    "oak": ("550", 550),
    "maple": ("770", 770),
    "sequoia": ("990", 990),
    "fern": ("1200", 1200),
}

# Renovation status mapping (for rent lookup)
RENOVATION_MAP = {0: "PR", 1: "Yes", 2: "No"}

# TEST MODE: Set to None for full batch, or a number to limit
TEST_LIMIT = None

# =============================================================================
# RESMAN API FUNCTIONS
# =============================================================================

def get_property_id(abbreviation):
    """Get PropertyID from ResMan by abbreviation"""
    url = f"{RESMAN_BASE_URL}/Account/GetProperties"
    payload = {
        "IntegrationPartnerID": RESMAN_PARTNER_ID,
        "APIKey": RESMAN_API_KEY,
        "AccountID": RESMAN_ACCOUNT_ID
    }
    response = requests.post(url, json=payload, headers={"Accept": "application/json"})
    if response.ok:
        props = response.json().get("Properties", [])
        for p in props:
            if p.get("Abbreviation") == abbreviation:
                return p["PropertyID"]
    return None


def fetch_leases(property_id):
    """Fetch leases from ResMan Partners API"""
    headers = {
        "ResMan-Account-Id": RESMAN_ACCOUNT_ID,
        "Accept": "application/json",
    }
    since_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    params = {
        "propertyId": property_id,
        "includeLeaseHistory": "true",
        "modifiedSince": since_date
    }
    resp = requests.get(
        f"{RESMAN_PARTNERS_URL}/Leasing/Leases",
        headers=headers,
        params=params,
        auth=HTTPBasicAuth(RESMAN_PARTNER_ID, RESMAN_API_KEY),
        timeout=60
    )
    if resp.ok:
        return resp.json().get("leases", [])
    return []


def filter_leases(leases, target_month):
    """Filter leases by expiration month and status"""
    filtered = []
    for lease in leases:
        end_date = lease.get("leaseEndDate", "")
        status = lease.get("leaseStatus", "").lower()

        # Check if ends in target month
        if not end_date or not end_date.startswith(target_month):
            continue

        # Check status
        status_match = any(t in status for t in TARGET_STATUSES)
        if status_match:
            filtered.append(lease)

    return filtered




def fetch_recurring_charges(property_id, billing_account_id, debug=False):
    """Fetch recurring charges (Pet Rent, Parking, WD) from Ledger API using billingAccountId"""
    headers = {
        "ResMan-Account-Id": RESMAN_ACCOUNT_ID,
        "Accept": "application/json",
    }
    # Fetch from first day of current month only
    first_of_month = datetime.now().replace(day=1).strftime("%Y-%m-%d 00:00:00")
    params = {
        "propertyId": property_id,
        "billingAccountId": billing_account_id,
        "postedSince": first_of_month
    }

    pet_rent = 0
    parking = 0
    wd = 0
    all_categories = set()

    try:
        resp = requests.get(
            f"{RESMAN_PARTNERS_URL}/Transactions/Ledger",
            headers=headers,
            params=params,
            auth=HTTPBasicAuth(RESMAN_PARTNER_ID, RESMAN_API_KEY),
            timeout=30
        )
        if resp.ok:
            transactions = resp.json().get("transactions", [])
            if debug:
                print(f"    [DEBUG] Total transactions: {len(transactions)}")

            for t in transactions:
                cat_obj = t.get("category", {})
                cat_name = (cat_obj.get("name", "") if isinstance(cat_obj, dict) else str(cat_obj)).lower()
                amount = t.get("amount", 0) or 0
                t_type = t.get("type", "")

                # Collect all categories for debugging
                if cat_name:
                    all_categories.add(cat_name)

                # Only look at Charges, not Payments
                if t_type != "Charge":
                    continue

                # Pet rent only (not pet fee)
                if "pet rent" in cat_name:
                    pet_rent = max(pet_rent, amount)
                elif "parking" in cat_name or "reserved" in cat_name:
                    parking = max(parking, amount)
                elif "washer" in cat_name or "dryer" in cat_name or "w/d" in cat_name:
                    wd = max(wd, amount)

            if debug:
                print(f"    [DEBUG] Categories found: {all_categories}")
        else:
            if debug:
                print(f"    [DEBUG] API error: {resp.status_code}")
    except Exception as e:
        print(f"    Ledger API error: {e}")

    return pet_rent, parking, wd


def fetch_deposit(property_id, billing_account_id):
    """Fetch deposit from DepositLedger API using billingAccountId"""
    headers = {
        "ResMan-Account-Id": RESMAN_ACCOUNT_ID,
        "Accept": "application/json",
    }
    params = {
        "propertyId": property_id,
        "billingAccountId": billing_account_id,
        "postedSince": "2020-01-01 00:00:00"  # Far back to get all deposits
    }

    try:
        resp = requests.get(
            f"{RESMAN_PARTNERS_URL}/Transactions/DepositLedger",
            headers=headers,
            params=params,
            auth=HTTPBasicAuth(RESMAN_PARTNER_ID, RESMAN_API_KEY),
            timeout=30
        )
        if resp.ok:
            # Response uses "transactions" not "depositTransactions"
            transactions = resp.json().get("transactions", [])
            return sum(t.get("amount", 0) for t in transactions)
    except Exception as e:
        print(f"    Deposit API error: {e}")

    return 0


def normalize_sqft_key(sqft_str, table):
    """Normalize sqft key - try exact match first, then strip letter suffix"""
    if not sqft_str:
        return ""
    # Try exact match first (e.g., "818V")
    if sqft_str in table:
        return sqft_str
    # Try stripping letter suffix (e.g., "707D" -> "707")
    numeric_only = re.match(r"(\d+)", sqft_str)
    if numeric_only:
        base_key = numeric_only.group(1)
        if base_key in table:
            return base_key
    return sqft_str


# Per-run cache of Rent & Fees values, keyed by Monday item id.
# Populated once per import via prefetch_rent_fees() so we don't re-hit Monday per lease.
_rent_fees_cache = {}  # {item_id: {"No": float, "PR": float, "Yes": float}}


def prefetch_rent_fees(property_abbr):
    """Fetch all Rent & Fees rows for a property in one Monday call and cache them.
    Must be called once at the start of a property run before lookup_rent_fees.
    """
    item_map = ITEM_IDS.get(property_abbr, {})
    if not item_map:
        return
    item_ids = list({v for v in item_map.values()})
    ids_str = ",".join(str(i) for i in item_ids)
    query = f'{{ items(ids:[{ids_str}]) {{ id column_values(ids:["numbers","numbers5","numbers0"]) {{ id text }} }} }}'
    resp = requests.post(
        MONDAY_API_URL,
        json={"query": query},
        headers={"Authorization": MONDAY_API_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Monday API error fetching Rent & Fees: {data['errors']}")
    for it in data.get("data", {}).get("items", []):
        row = {}
        for cv in it.get("column_values", []):
            for label, col_id in RENT_FEES_COLUMN_IDS.items():
                if cv["id"] == col_id:
                    txt = (cv.get("text") or "").strip()
                    try:
                        row[label] = float(txt) if txt else None
                    except ValueError:
                        row[label] = None
        _rent_fees_cache[int(it["id"])] = row


def lookup_rent_fees(property_abbr, sqft_str, renovation_index):
    """Lookup rent live from the Rent & Fees board (via prefetch cache).
    Returns 0 if the property/sqft has no ITEM_ID mapping or the board cell is empty.
    """
    item_map = ITEM_IDS.get(property_abbr, {})
    if not item_map:
        return 0
    sqft_key = normalize_sqft_key(sqft_str, item_map)
    item_id = item_map.get(sqft_key)
    if item_id is None:
        return 0
    row = _rent_fees_cache.get(item_id)
    if not row:
        return 0
    renovated = RENOVATION_MAP.get(renovation_index, "No")
    val = row.get(renovated)
    return val if val is not None else 0


def get_item_id(property_abbr, sqft_str):
    """Get Monday.com item ID for connect_boards column"""
    if property_abbr not in ITEM_IDS:
        return None
    table = ITEM_IDS[property_abbr]
    sqft_key = normalize_sqft_key(sqft_str, table)
    return table.get(sqft_key)


# =============================================================================
# DATA TRANSFORMATION FUNCTIONS
# =============================================================================

def parse_unit_type(unit_type_name, property_abbr="ORT"):
    """
    Parse unit type name to extract SQFT and renovation status

    ORT format: "1000 sqft Classic - 2 Bed 1 Bath" -> (1000, "1000", 2)
    49thStr format: "707D Renovated - 2 Bed 1 Bath" -> (707, "707D", 1)
    MP format: "Jasmine Renovated - 1 Bed 1.5 Bath" -> (935, "935", 1)

    Renovation mapping:
    - Classic -> index 2 (No)
    - Partial -> index 0 (PR)
    - Renovated -> index 1 (Yes)
    """
    sqft = None
    sqft_str = None
    renovation_index = None

    pattern_type = UNIT_TYPE_PATTERN.get(property_abbr, "sqft")

    if pattern_type == "flower":
        # MP format: "Jasmine Renovated" or "Lily Classic"
        unit_lower = unit_type_name.lower()
        for flower, (sqft_key, sqft_val) in FLOWER_TO_SQFT.items():
            if flower in unit_lower:
                sqft_str = sqft_key
                sqft = sqft_val
                break
    elif pattern_type == "tree":
        # AP format: "Maple Renovated" or "Oak Classic"
        unit_lower = unit_type_name.lower()
        for tree, (sqft_key, sqft_val) in TREE_TO_SQFT.items():
            if tree in unit_lower:
                sqft_str = sqft_key
                sqft = sqft_val
                break
    elif pattern_type == "simple":
        # TRA format: "750 - 2/1" or "750C - 2/1" (SQFT - Beds/Baths)
        # Extract number (with optional letter suffix) at start
        match = re.match(r"(\d+[A-Z]?)", unit_type_name, re.IGNORECASE)
        if match:
            sqft_str = match.group(1).upper()
            num_match = re.match(r"(\d+)", sqft_str)
            if num_match:
                sqft = int(num_match.group(1))
    elif pattern_type == "prefix":
        # 49th Street format: "707D Renovated" or "818V Renovated"
        # Extract number (with optional letter suffix) at start
        match = re.match(r"(\d+[A-Z]?)", unit_type_name, re.IGNORECASE)
        if match:
            sqft_str = match.group(1).upper()
            # Extract just the number for SQFT column
            num_match = re.match(r"(\d+)", sqft_str)
            if num_match:
                sqft = int(num_match.group(1))
    else:
        # ORT format: "1000 sqft Classic" or EAST format: "750 Sf Ft" or "850 (L) Sf Ft"
        match = re.search(r"(\d+)\s*(?:\([UL]\)\s*)?(?:sqft|sf\s*ft)", unit_type_name, re.IGNORECASE)
        if match:
            sqft = int(match.group(1))
            sqft_str = str(sqft)

    # Determine renovation status
    unit_lower = unit_type_name.lower()
    if "partial" in unit_lower:
        renovation_index = 0  # PR
    elif "classic" in unit_lower:
        renovation_index = 2  # No
    elif "renovated" in unit_lower:
        renovation_index = 1  # Yes

    return sqft, sqft_str, renovation_index


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


def get_current_rent_info(history):
    """Get rent and other charges from most recent history entry"""
    if not history:
        return 0, 0

    # History is ordered, last entry is most recent
    current = history[-1]
    rent = current.get("rent", 0) or 0
    other_charges = current.get("otherCharges", 0) or 0
    return rent, other_charges


def get_status_renewal_index(lease):
    """
    Determine status renewal index based on lease status
    - NTV (Notice to Vacate) -> index 0
    - Under Eviction -> index 3
    - Otherwise -> None (leave blank)
    """
    status = lease.get("leaseStatus", "").lower()
    notice_date = lease.get("noticeToVacateDate")

    if "eviction" in status:
        return 3  # Under Eviction
    elif "notice" in status or notice_date:
        return 0  # NTV

    return None  # Leave blank


def map_lease_to_columns(lease, property_abbr, property_id):
    """Map ResMan lease to Monday.com column values"""
    # Parse unit type (pass property_abbr for pattern selection)
    unit_type_name = lease.get("unitTypeName", "")
    sqft, sqft_str, renovation_index = parse_unit_type(unit_type_name, property_abbr)

    # Get resident name
    people = lease.get("people", [])
    resident_name = get_resident_name(people)

    # Get rent info from history
    history = lease.get("history", [])
    base_rent, other_charges = get_current_rent_info(history)
    current_total = base_rent + other_charges

    # Get dates
    move_in_date = lease.get("moveInDate", "")
    exp_date = lease.get("leaseEndDate", "")

    # Get status renewal
    status_renewal_index = get_status_renewal_index(lease)

    # Fetch recurring charges using billingAccountId
    billing_account_id = lease.get("billingAccountId", "")
    pet, parking, wd = fetch_recurring_charges(property_id, billing_account_id, debug=False)

    # Fetch deposit using billingAccountId
    deposit = fetch_deposit(property_id, billing_account_id)

    # Lookup rent & fees (use sqft_str for lookup key)
    rent_fees = lookup_rent_fees(property_abbr, sqft_str, renovation_index)

    # Get item ID for connect_boards (use sqft_str for lookup key)
    item_id = get_item_id(property_abbr, sqft_str)

    # Get property-specific column IDs
    prop_cols = COLUMN_IDS.get(property_abbr, COLUMN_IDS.get("ORT", {}))
    col_status_renewal = prop_cols.get("status_renewal", "status9")
    col_rent_fees = prop_cols.get("rent_fees", "numbers88")
    col_deposit = prop_cols.get("deposit", "numbers9")

    # Build column values
    columns = {
        "text": resident_name,  # Resident_Name
    }

    # Add dates
    if move_in_date:
        columns["date4"] = {"date": move_in_date}  # MI Date
    if exp_date:
        columns["date"] = {"date": exp_date}  # Exp_Date

    # Add numbers
    if base_rent:
        columns["numbers69"] = str(base_rent)  # Base Rent
    if current_total:
        columns["numbers8"] = str(current_total)  # Current Total
    if sqft:
        columns["numbers"] = str(sqft)  # SQFT
    if rent_fees:
        columns[col_rent_fees] = str(rent_fees)  # Rent & Fees (property-specific column)
    if deposit:
        columns[col_deposit] = str(deposit)  # Deposit (property-specific column)
    if pet:
        columns["numbers41"] = str(pet)  # Pet
    if parking:
        columns["numbers_1"] = str(parking)  # Parking
    if wd:
        columns["numbers48"] = str(wd)  # WD

    # Add status columns
    if renovation_index is not None:
        columns["status"] = {"index": renovation_index}  # Renovated
    if status_renewal_index is not None:
        columns[col_status_renewal] = {"index": status_renewal_index}  # Status Renewal (property-specific)

    # Add connect_boards (link to Rent & fees item)
    if item_id:
        columns["connect_boards"] = {"item_ids": [item_id]}

    return columns, {"deposit": deposit, "pet": pet, "parking": parking, "wd": wd, "rent_fees": rent_fees, "sqft_str": sqft_str}


# =============================================================================
# MONDAY.COM API FUNCTIONS
# =============================================================================

def create_monday_group(board_id, group_name):
    """Create a new group in Monday.com board and return the group_id"""
    query = f'''
    mutation {{
        create_group (
            board_id: {board_id},
            group_name: "{group_name}"
        ) {{
            id
        }}
    }}
    '''

    headers = {
        "Authorization": MONDAY_API_KEY,
        "Content-Type": "application/json"
    }

    response = requests.post(
        MONDAY_API_URL,
        json={"query": query},
        headers=headers
    )

    if response.ok:
        data = response.json()
        if "errors" in data:
            return None, data["errors"]
        return data.get("data", {}).get("create_group", {}).get("id"), None

    return None, response.text


def create_monday_item(board_id, item_name, column_values, group_id=None):
    """Create an item in Monday.com board"""
    # Escape the column values JSON for GraphQL
    columns_json = json.dumps(column_values).replace('"', '\\"')

    # Build query with optional group_id
    group_param = f', group_id: "{group_id}"' if group_id else ''

    query = f'''
    mutation {{
        create_item (
            board_id: {board_id},
            item_name: "{item_name}"{group_param},
            column_values: "{columns_json}"
        ) {{
            id
        }}
    }}
    '''

    headers = {
        "Authorization": MONDAY_API_KEY,
        "Content-Type": "application/json"
    }

    response = requests.post(
        MONDAY_API_URL,
        json={"query": query},
        headers=headers
    )

    if response.ok:
        data = response.json()
        if "errors" in data:
            return None, data["errors"]
        return data.get("data", {}).get("create_item", {}).get("id"), None

    return None, response.text


# =============================================================================
# MAIN
# =============================================================================

def main():
    # Parse arguments
    if len(sys.argv) < 3:
        print("Usage: python create_monday_items.py <PROPERTY_ABBR> <MONTH>")
        print("Example: python create_monday_items.py ORT 2026-08")
        print("Example: python create_monday_items.py 49thStr 2026-08")
        sys.exit(1)

    property_abbr_input = sys.argv[1]
    property_abbr = property_abbr_input.upper()  # Normalize for dict lookups
    target_month = sys.argv[2]  # Format: YYYY-MM

    # Validate property
    if property_abbr not in BOARD_IDS:
        print(f"Error: Unknown property '{property_abbr}'")
        print(f"Available properties: {list(BOARD_IDS.keys())}")
        sys.exit(1)

    board_id = BOARD_IDS[property_abbr]
    print(f"Property: {property_abbr_input} ({property_abbr})")
    print(f"Target Month: {target_month}")
    print(f"Monday.com Board ID: {board_id}")
    print("-" * 50)

    # Step 1: Get Property ID (use original input for ResMan lookup)
    print("Fetching property ID from ResMan...")
    property_id = get_property_id(property_abbr_input)
    if not property_id:
        print(f"Error: Could not find property '{property_abbr}' in ResMan")
        sys.exit(1)
    print(f"Property ID: {property_id}")

    # Step 2: Fetch leases
    print("Fetching leases from ResMan...")
    all_leases = fetch_leases(property_id)
    print(f"Total leases fetched: {len(all_leases)}")

    # Step 3: Filter leases
    filtered_leases = filter_leases(all_leases, target_month)
    print(f"Leases expiring in {target_month}: {len(filtered_leases)}")

    # Apply test limit if set
    if TEST_LIMIT:
        filtered_leases = filtered_leases[:TEST_LIMIT]
        print(f"TEST MODE: Limited to {TEST_LIMIT} lease(s)")

    print("-" * 50)

    # Prefetch Rent & Fees from Monday board (source of truth)
    print("Fetching Rent & Fees from Monday.com board...")
    prefetch_rent_fees(property_abbr)
    print(f"Cached rent for {len(_rent_fees_cache)} unit type(s)")
    print("-" * 50)

    # Step 4: Create a new group for this import
    group_name = f"Imported - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    print(f"Creating group: {group_name}")
    group_id, error = create_monday_group(board_id, group_name)
    if group_id:
        print(f"Group created (ID: {group_id})")
    else:
        print(f"Warning: Could not create group: {error}")
        print("Items will be added to default group")

    print("-" * 50)

    # Step 5: Create Monday.com items
    created = 0
    failed = 0

    for i, lease in enumerate(filtered_leases, 1):
        unit_number = lease.get("unitNumber", "Unknown")
        print(f"\n[{i}/{len(filtered_leases)}] Processing {unit_number}...")

        # Map to columns (includes API calls for deposit and charges)
        column_values, extras = map_lease_to_columns(lease, property_abbr, property_id)
        print(f"    Deposit: ${extras['deposit']}, Pet: ${extras['pet']}, Parking: ${extras['parking']}, WD: ${extras['wd']}, Rent&Fees: ${extras['rent_fees']}")

        # Create item in the new group
        item_id, error = create_monday_item(board_id, unit_number, column_values, group_id)

        if item_id:
            print(f"    [OK] Created item (ID: {item_id})")
            created += 1
        else:
            print(f"    [FAIL] {error}")
            failed += 1

    # Summary
    print("\n" + "=" * 50)
    print(f"Summary: {created} created, {failed} failed")


if __name__ == "__main__":
    main()
