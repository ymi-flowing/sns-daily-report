"""Persist and age-track dashboard alerts across refresh runs.

Each alert is keyed by (property_abbr, list_name, unit_id). On every refresh:
- A key present in the previous state AND fired again now keeps its firstSeen date.
- A key fired now but not in previous state is new (firstSeen = today).
- A key in previous state but not fired now is dropped (so a reappearance later
  counts as a new alert).

State file shape:
    {
      "updated": "YYYY-MM-DD",
      "alerts": {
        "<abbr>|<listName>|<unitId>": {
          "firstSeen": "YYYY-MM-DD",
          "lastMsg": "..."
        },
        ...
      }
    }
"""
import json
import os
from datetime import date, datetime


ALERT_LIST_KEYS = ("vacantNotReadyList", "ntvList", "vacantLeasedList", "ntvLeasedList", "insufficientNoticeList", "pendingInRealUnitList", "approvedPaidInHoldingList", "holdingOverListedList")


def _parse_date(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def load_alert_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("alerts", {}) or {}
    except (OSError, ValueError):
        return {}


def _state_key(abbr, list_name, unit_id):
    return f"{abbr}|{list_name}|{unit_id or ''}"


def apply_state_to_properties(properties_data, prev_state, today):
    """Decorate every alert'd unit dict with firstSeen + daysAgo, and return
    the new state dict to persist. Mutates properties_data in place."""
    new_state = {}
    for abbr, data in properties_data.items():
        if not isinstance(data, dict):
            continue
        for list_name in ALERT_LIST_KEYS:
            items = data.get(list_name) or []
            for item in items:
                if not item.get("hasAlert"):
                    continue
                key = _state_key(abbr, list_name, item.get("unitId"))
                prev = prev_state.get(key)
                if prev and _parse_date(prev.get("firstSeen")):
                    first_seen = _parse_date(prev["firstSeen"])
                else:
                    first_seen = today
                days_ago = (today - first_seen).days
                item["firstSeen"] = first_seen.isoformat()
                item["daysAgo"] = days_ago
                new_state[key] = {
                    "firstSeen": first_seen.isoformat(),
                    "lastMsg": item.get("alertMsg", ""),
                }
    return new_state


def write_alert_state(path, state, today):
    payload = {
        "updated": today.isoformat(),
        "alerts": state,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
