"""
Google Sheets client — talks to the Apps Script webhook deployed on the target sheet.

Reads SHEETS_WEBHOOK_URL and SHARED_SECRET from .env in the project root.
"""
import json
import os
import time
from pathlib import Path

import requests

_ENV_LOADED = False


def _load_env():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _ENV_LOADED = True


def _config():
    _load_env()
    url = os.environ.get("SHEETS_WEBHOOK_URL")
    secret = os.environ.get("SHARED_SECRET")
    if not url:
        raise RuntimeError("SHEETS_WEBHOOK_URL missing from .env")
    if not secret:
        raise RuntimeError("SHARED_SECRET missing from .env")
    return url, secret


def _post(payload: dict, retries: int = 3) -> dict:
    """
    Only retry on ConnectionError (request never reached the server).
    Do NOT retry on Timeout/ReadTimeout — those may mean the server already
    processed the request and the retry would duplicate the write.
    """
    url, secret = _config()
    payload = dict(payload)
    payload["secret"] = secret
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=120, allow_redirects=True)
        except requests.exceptions.ConnectionError as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"webhook connection failed after {retries} tries: {e}") from e
        # From here down, the server got the request — never retry.
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"webhook error: {data.get('error')}")
        return data
    raise RuntimeError(f"webhook failed after {retries} tries: {last_err}")


def list_tabs() -> list[str]:
    return _post({"action": "list_tabs"})["tabs"]


def ensure_tab(name: str, headers: list[str]) -> dict:
    return _post({"action": "ensure_tab", "tab": name, "headers": headers})


def append_rows(name: str, rows: list[list], batch_size: int = 500) -> int:
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        result = _post({"action": "append_rows", "tab": name, "rows": chunk})
        total += result.get("appended", 0)
    return total


def get_column(name: str, header: str) -> list:
    return _post({"action": "get_column", "tab": name, "header": header})["values"]


def update_column(name: str, header: str, values: list) -> dict:
    return _post({"action": "update_column", "tab": name, "header": header, "values": values})


def inspect_tab(name: str) -> dict:
    return _post({"action": "inspect_tab", "tab": name})


def get_range(name: str, range_a1: str) -> dict:
    return _post({"action": "get_range", "tab": name, "range": range_a1})


def set_range(name: str, range_a1: str, values: list, mode: str = "values") -> dict:
    return _post({"action": "set_range", "tab": name, "range": range_a1, "values": values, "mode": mode})


def rename_column(name: str, from_header: str, to_header: str) -> dict:
    return _post({"action": "rename_column", "tab": name, "from": from_header, "to": to_header})


def format_column(name: str, header: str, format_pattern: str) -> dict:
    return _post({"action": "format_column", "tab": name, "header": header, "format": format_pattern})


def insert_column(name: str, header: str, after: str | None = None, values: list | None = None) -> dict:
    return _post({"action": "insert_column", "tab": name, "header": header, "after": after, "values": values or []})


def set_formulas(name: str, header: str, formulas: list[str]) -> dict:
    return _post({"action": "set_formulas", "tab": name, "header": header, "formulas": formulas})


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "ping":
        print(list_tabs())
    else:
        print("Usage: python sheets_client.py ping")
