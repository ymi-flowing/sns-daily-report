"""
Vercel Serverless Function - Redirect to a FRESH signed PDF URL for a ResMan invoice.

Endpoint: GET /api/invoice_pdf?propertyId=<guid>&invoiceId=<guid>

ResMan's /Invoices response includes an `imageUrl` that is a short-lived
(~30 min) Azure Blob SAS URL. Those URLs are baked into invoices_data.json
once every refresh, so most clicks hit an expired URL. This endpoint
re-queries /Invoices with the propertyId and finds the matching invoice,
then 302-redirects to its fresh imageUrl.
"""
from http.server import BaseHTTPRequestHandler
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request


API_KEY = os.environ.get("RESMAN_API_KEY", "")
PARTNER_ID = os.environ.get("RESMAN_PARTNER_ID", "")
ACCOUNT_ID = os.environ.get("RESMAN_ACCOUNT_ID", "")


class handler(BaseHTTPRequestHandler):
    def _plain(self, status, msg):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(msg.encode("utf-8"))

    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        p = urllib.parse.parse_qs(q)
        property_id = (p.get("propertyId") or [""])[0]
        invoice_id = (p.get("invoiceId") or [""])[0]
        if not property_id or not invoice_id:
            return self._plain(400, "propertyId and invoiceId are required")

        if not (API_KEY and PARTNER_ID and ACCOUNT_ID):
            return self._plain(500, "ResMan credentials not configured")

        # Wide modifiedSince so the invoice is returned regardless of how long ago it was modified.
        params = urllib.parse.urlencode({
            "propertyId": property_id,
            "modifiedSince": "2020-01-01 00:00:00",
        })
        url = f"https://partners-api.myresman.com/Invoices?{params}"
        auth = base64.b64encode(f"{PARTNER_ID}:{API_KEY}".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("ResMan-Account-Id", ACCOUNT_ID)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "SNS-Invoice-Pdf-Proxy")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return self._plain(e.code, f"ResMan /Invoices HTTP {e.code}")
        except Exception as e:
            return self._plain(502, f"ResMan /Invoices failed: {e}")

        invoices = data.get("invoices") or []
        match = next((inv for inv in invoices if inv.get("invoiceId") == invoice_id), None)
        if not match:
            return self._plain(404, "invoice not found for that property")
        image_url = match.get("imageUrl")
        if not image_url:
            return self._plain(404, "invoice has no PDF attachment")

        # 302 to the fresh signed URL.
        self.send_response(302)
        self.send_header("Location", image_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
