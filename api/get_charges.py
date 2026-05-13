"""
Vercel Serverless Function - Get Deposit and Recurring Charges for a Unit
Endpoint: GET /api/get_charges?propertyId=XXX&unitId=YYY
"""
from http.server import BaseHTTPRequestHandler
import os
import json
import urllib.request
import urllib.error
import urllib.parse
import base64
from datetime import datetime


# ResMan API Credentials
RESMAN_API_KEY = os.environ.get("RESMAN_API_KEY", "")
RESMAN_PARTNER_ID = os.environ.get("RESMAN_PARTNER_ID", "")
RESMAN_ACCOUNT_ID = os.environ.get("RESMAN_ACCOUNT_ID", "")
RESMAN_PARTNERS_URL = "https://partners-api.myresman.com"


def make_request(url, headers=None, auth=None):
    """Make HTTP GET request and return response JSON"""
    if headers is None:
        headers = {}

    if auth:
        credentials = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers['Authorization'] = f'Basic {credentials}'

    req = urllib.request.Request(url, method='GET')
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        response = urllib.request.urlopen(req, timeout=30)
        return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def fetch_deposit(property_id, lease_id):
    """Fetch deposit from DepositLedger API"""
    params = urllib.parse.urlencode({
        "propertyId": property_id,
        "leaseId": lease_id
    })
    url = f"{RESMAN_PARTNERS_URL}/Transactions/DepositLedger?{params}"

    result = make_request(
        url,
        headers={"ResMan-Account-Id": RESMAN_ACCOUNT_ID, "Accept": "application/json"},
        auth=(RESMAN_PARTNER_ID, RESMAN_API_KEY)
    )

    if "error" in result:
        return 0, result.get("error")

    transactions = result.get("depositTransactions", [])
    total = sum(t.get("amount", 0) for t in transactions)
    return total, None


def fetch_recurring_charges(property_id, unit_id):
    """Fetch recurring charges (Pet Rent, Parking, WD) from Ledger API"""
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
        return 0, 0, 0, result.get("error")

    transactions = result.get("transactions", [])

    pet_rent = 0
    parking = 0
    wd = 0
    categories_found = []

    for t in transactions:
        cat = (t.get("category") or "").lower()
        amount = t.get("amount", 0) or 0

        # Collect all categories for debugging
        if cat and cat not in categories_found:
            categories_found.append(cat)

        if "pet rent" in cat:
            pet_rent += amount
        elif "parking" in cat or "reserved" in cat:
            parking += amount
        elif "washer" in cat or "dryer" in cat or "w/d" in cat:
            wd += amount

    return pet_rent, parking, wd, categories_found if categories_found else None


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

            property_id = params.get("propertyId", [""])[0]
            unit_id = params.get("unitId", [""])[0]
            lease_id = params.get("leaseId", [""])[0]

            if not property_id or not unit_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing propertyId or unitId"}).encode())
                return

            # Fetch recurring charges only (deposit API not available - user enters manually)
            pet, parking, wd, charges_err = fetch_recurring_charges(property_id, unit_id)

            # Return response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "deposit": 0,  # User enters manually
                "pet": pet,
                "parking": parking,
                "wd": wd,
                "debug": {
                    "categories": charges_err,  # Now contains list of categories found
                    "propertyId": property_id,
                    "unitId": unit_id
                }
            }).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
