"""
Vercel Serverless Function - Push Leases to Monday.com
Endpoint: POST /api/push_monday
"""
from http.server import BaseHTTPRequestHandler
import os
import json
import urllib.request
import urllib.error


# =============================================================================
# CONFIGURATION
# =============================================================================

# Monday.com API
MONDAY_API_KEY = os.environ.get("MONDAY_API_KEY", "")
MONDAY_API_URL = "https://api.monday.com/v2"

# Property to Board ID mapping
BOARD_IDS = {
    "ORT": 5562214499,
    # Add more as needed:
    # "49thStr": TBD,
    # "ROP": TBD,
    # "AP": TBD,
    # "SJ": TBD,
    # "EAST": TBD,
    # "MOC": TBD,
    # "TV": TBD,
    # "MP": TBD,
    # "MUSO": TBD,
    # "TRA": TBD,
    # "AN3": TBD,
}

# Renovation status index mapping
RENOVATION_INDEX = {
    "No": 2,
    "PR": 0,
    "Yes": 1
}

# Status renewal index mapping
STATUS_INDEX = {
    "NTV": 0,
    "Under Eviction": 3
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_monday_item(board_id, item_name, column_values):
    """Create an item in Monday.com board"""
    # Escape the column values JSON for GraphQL
    columns_json = json.dumps(column_values).replace('"', '\\"')

    query = f'''
    mutation {{
        create_item (
            board_id: {board_id},
            item_name: "{item_name}",
            column_values: "{columns_json}"
        ) {{
            id
        }}
    }}
    '''

    data = json.dumps({"query": query}).encode('utf-8')

    req = urllib.request.Request(MONDAY_API_URL, data=data, method='POST')
    req.add_header('Authorization', MONDAY_API_KEY)
    req.add_header('Content-Type', 'application/json')

    try:
        response = urllib.request.urlopen(req, timeout=30)
        result = json.loads(response.read().decode())

        if "errors" in result:
            return None, result["errors"]

        item_id = result.get("data", {}).get("create_item", {}).get("id")
        return item_id, None

    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()}"
    except Exception as e:
        return None, str(e)


def map_lease_to_columns(lease):
    """Map lease data to Monday.com column values"""
    columns = {
        "text": lease.get("resident", ""),  # Resident_Name
    }

    # Add dates
    if lease.get("moveInDate"):
        columns["date4"] = {"date": lease["moveInDate"]}  # MI Date
    if lease.get("expDate"):
        columns["date"] = {"date": lease["expDate"]}  # Exp_Date

    # Add numbers
    if lease.get("baseRent"):
        columns["numbers69"] = str(lease["baseRent"])  # Base Rent
    if lease.get("currentTotal"):
        columns["numbers8"] = str(lease["currentTotal"])  # Current Total
    if lease.get("sqft"):
        columns["numbers"] = str(lease["sqft"])  # SQFT
    if lease.get("rentFees"):
        columns["numbers88"] = str(lease["rentFees"])  # Rent & Fees
    if lease.get("deposit"):
        columns["numbers9"] = str(lease["deposit"])  # Deposit
    if lease.get("pet"):
        columns["numbers41"] = str(lease["pet"])  # Pet
    if lease.get("parking"):
        columns["numbers_1"] = str(lease["parking"])  # Parking
    if lease.get("wd"):
        columns["numbers48"] = str(lease["wd"])  # WD

    # Add status columns
    renovated = lease.get("renovated", "No")
    if renovated in RENOVATION_INDEX:
        columns["status"] = {"index": RENOVATION_INDEX[renovated]}  # Renovated

    status = lease.get("status", "Current")
    if status in STATUS_INDEX:
        columns["status9"] = {"index": STATUS_INDEX[status]}  # Status Renewal

    # Add connect_boards (link to Rent & fees item)
    if lease.get("itemId"):
        columns["connect_boards"] = {"item_ids": [lease["itemId"]]}

    return columns


# =============================================================================
# MAIN HANDLER
# =============================================================================

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)

            property_abbr = data.get("property", "").upper()
            leases = data.get("leases", [])

            if not property_abbr:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing property"}).encode())
                return

            if property_abbr not in BOARD_IDS:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": f"Board ID not configured for property '{property_abbr}'",
                    "available": list(BOARD_IDS.keys())
                }).encode())
                return

            if not leases:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No leases provided"}).encode())
                return

            board_id = BOARD_IDS[property_abbr]
            results = []

            # Create items in Monday.com
            for lease in leases:
                unit = lease.get("unit", "Unknown")
                column_values = map_lease_to_columns(lease)

                item_id, error = create_monday_item(board_id, unit, column_values)

                if item_id:
                    results.append({
                        "unit": unit,
                        "success": True,
                        "itemId": item_id
                    })
                else:
                    results.append({
                        "unit": unit,
                        "success": False,
                        "error": str(error)
                    })

            # Count successes and failures
            success_count = sum(1 for r in results if r["success"])
            fail_count = sum(1 for r in results if not r["success"])

            # Return response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "property": property_abbr,
                "boardId": board_id,
                "total": len(leases),
                "success": success_count,
                "failed": fail_count,
                "results": results
            }).encode())

        except json.JSONDecodeError as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON: {str(e)}"}).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
