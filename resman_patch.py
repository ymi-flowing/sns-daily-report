"""Partners API PATCH helpers for the SNS Daily Report refresh.

Mirrors the proven implementation in `Av Dates/sync_av_dates.py` but reuses this
project's env-driven credentials and the standard retry cadence.
"""
import json
import os
import time

import requests
from requests.auth import HTTPBasicAuth


API_KEY = os.environ.get("RESMAN_API_KEY", "43XzRFJkhvAJGLvR6GlJUpKy6nyc")
PARTNER_ID = os.environ.get("RESMAN_PARTNER_ID", "SNSaPi")
ACCOUNT_ID = os.environ.get("RESMAN_ACCOUNT_ID", "1550")
PARTNERS_API_URL = "https://partners-api.myresman.com"

API_TIMEOUT = 30
API_MAX_RETRIES = 2
API_RETRY_DELAY = 3
API_CALL_DELAY = 0.5


def patch_date_available(property_id, unit_id, new_date_iso, description=""):
    """PATCH /Units/DateAvailable. Returns (ok: bool, error: str|None)."""
    url = f"{PARTNERS_API_URL}/Units/DateAvailable"
    headers = {
        "ResMan-Account-Id": ACCOUNT_ID,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {"propertyId": property_id, "unitId": unit_id, "date": new_date_iso}

    time.sleep(API_CALL_DELAY)
    last_err = None
    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            resp = requests.patch(
                url,
                headers=headers,
                json=body,
                auth=HTTPBasicAuth(PARTNER_ID, API_KEY),
                timeout=API_TIMEOUT,
            )
            if 200 <= resp.status_code < 300:
                return True, None
            try:
                detail = resp.json()
                err = detail.get("message") or detail.get("error") or json.dumps(detail)[:200]
            except ValueError:
                err = (resp.text or "")[:200]
            last_err = f"HTTP {resp.status_code}: {err}"
            if resp.status_code < 500 and resp.status_code != 429:
                return False, last_err
        except requests.exceptions.RequestException as e:
            last_err = str(e)
        if attempt < API_MAX_RETRIES:
            time.sleep(API_RETRY_DELAY)
    return False, last_err or "unknown error"
