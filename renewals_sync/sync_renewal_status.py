"""
Sync Renewal Status (column F) and Actual Renewal (column S) from ResMan.

For every row in the property tab:
  - Fetch that unit's leases from ResMan.
  - Match the resident by name (fuzzy).
  - Overwrite column F with the current ResMan status (mapped to sheet convention).
  - If a renewal is detected (matched lease's endDate > row Exp Date + 60 days),
    fill column S with the renewal lease's total charges (rent + otherCharges).
  - Otherwise leave S empty (or clear existing value if --clear-empty).

Usage:
  python sync_renewal_status.py 49THSTR --dry-run
  python sync_renewal_status.py 49THSTR
"""
import argparse
import sys
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from create_monday_items import (
    fetch_leases, get_property_id, get_resident_name,
)
from sheet_schema import HEADERS, tab_name_for
import sheets_client


# ResMan status -> sheet convention. "Current + renewed" is handled separately.
STATUS_MAP = {
    "Current":          "Current",
    "Notice to Vacate": "NTV",
    "Under Eviction":   "Under Eviction",
    "Former":           "Moved Out",
    "Cancelled":        "Cancelled",
    "Evicted":          "Evicted",
    "Pending":          "Pending",
    "Denied":           "Denied",
}

# When picking among multiple leases for one unit, prefer active statuses.
STATUS_PRIORITY = {
    "Current": 0, "Notice to Vacate": 1, "Under Eviction": 2, "Pending": 3,
    "Former": 4, "Cancelled": 5, "Evicted": 6, "Denied": 7,
}

RENEWAL_START_GAP_DAYS = 30  # renewal.startDate <= expiring.endDate + N days


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def parse_iso_date(s: str):
    """ResMan date: '2026-09-07' or '2026-09-07T00:00:00'. Returns datetime or None."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def parse_expiring_month(s: str):
    """Column A cell -> (year, month) tuple, or None."""
    if not s:
        return None
    s = str(s).strip()
    # ISO date (Sheets stored 'October 2026' as 2026-10-01)
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
        return d.year, d.month
    except ValueError:
        pass
    # 'October 2026' text
    for fmt in ("%B %Y", "%b %Y"):
        try:
            d = datetime.strptime(s, fmt)
            return d.year, d.month
        except ValueError:
            continue
    return None


def _full_name(p: dict) -> str:
    return f"{p.get('firstName','')} {p.get('lastName','')}".strip()


def choose_lease_for_row(unit_leases: list, csv_name: str, name_threshold: float = 0.6):
    """
    Pick the best lease for a sheet row, matching csv_name against ANY person
    on the lease (past or present) — not just HoH. Returns (lease, similarity,
    matched_person). The matched_person tells us if OUR specific resident has
    a moveOutDate (roommate/turnover case).
    """
    best_lease = None
    best_person = None
    best_sim = 0.0
    for L in unit_leases:
        for p in L.get("people", []):
            name = _full_name(p)
            sim = name_similarity(csv_name, name)
            if sim > best_sim:
                best_sim = sim
                best_lease = L
                best_person = p
    if best_sim < name_threshold:
        return None, best_sim, None
    return best_lease, best_sim, best_person


def parse_sheet_date(s: str):
    """Sheet MI/Exp cells can be MM/DD/YYYY text or ISO with time. Returns datetime or None."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt)
        except ValueError:
            continue
    return None


def find_by_move_in_date(unit_leases: list, sheet_mi_date):
    """
    Fallback matcher: find a lease whose top-level moveInDate matches the sheet's
    MI Date exactly. Handles two turnover patterns:
      1. Name-change (marriage): same lease, same MI, different last name.
      2. Same MI-date data anomaly: sheet has a name that never appears in ResMan
         but the MI date matches uniquely to one lease.
    Returns the lease (only if exactly ONE lease matches by MI date) or None.
    """
    if not sheet_mi_date:
        return None
    matches = []
    for L in unit_leases:
        mi = parse_iso_date(L.get("moveInDate", ""))
        if mi and mi.date() == sheet_mi_date.date():
            matches.append(L)
    return matches[0] if len(matches) == 1 else None


def find_expiring_and_renewal(lease: dict, expiring_year: int, expiring_month: int):
    """
    Given a lease and the row's expiring (year, month), return
    (expiring_history_entry, renewal_history_entry_or_None).

    - expiring_entry = history entry whose endDate falls in the row's expiring month
    - renewal_entry  = the BEST next history entry whose startDate is within
                       RENEWAL_START_GAP_DAYS of expiring_entry.endDate

    "Best" matters because ResMan often leaves a Cancelled sibling with the
    same startDate as the signed Current/Renewed renewal (an older offer that
    was redone and signed). The sort key alone can land us on the Cancelled
    one. We now rank candidates: signed AND not cancelled > signed > not
    cancelled > latest applicationDate.
    """
    hist = sorted(
        lease.get("history", []),
        key=lambda h: (h.get("startDate") or "", h.get("endDate") or ""),
    )
    expiring_entry = None
    expiring_idx = -1
    for i, entry in enumerate(hist):
        end = parse_iso_date(entry.get("endDate", ""))
        if end and end.year == expiring_year and end.month == expiring_month:
            expiring_entry = entry
            expiring_idx = i
            # keep looking — if there are duplicates, prefer the latest
    if expiring_entry is None:
        return None, None
    e_end = parse_iso_date(expiring_entry["endDate"])
    if not e_end:
        return expiring_entry, None

    # Collect every history entry whose startDate lands in the renewal window.
    candidates = []
    for entry in hist[expiring_idx + 1:]:
        n_start = parse_iso_date(entry.get("startDate", ""))
        if n_start and 0 <= (n_start - e_end).days <= RENEWAL_START_GAP_DAYS:
            candidates.append(entry)
    if not candidates:
        return expiring_entry, None

    def rank(entry):
        signed = bool(entry.get("signedDate"))
        active = entry.get("status") not in ("Cancelled", "Denied")
        return (signed and active, signed, active, entry.get("applicationDate") or "")

    return expiring_entry, max(candidates, key=rank)


def renewal_total(entry: dict):
    if not entry:
        return None
    r = entry.get("rent") or 0
    o = entry.get("otherCharges") or 0
    return r + o


def lease_has_ended(lease: dict) -> bool:
    """
    Lease-level move-out trumps any stale 'renewal' history entry. ResMan sometimes
    leaves a signed-then-cancelled renewal in history[] even after the resident
    moves out. If leaseStatus is a terminal one OR moveOutDate is set, the tenancy
    is over — don't count history-entry renewals.

    Concrete case: 49thStr unit 1003 (WALDEIR DE SOUZA) — status=Former,
    moveOut=2026-08-04, but history has an entry start=2026-08-05 end=2027-08-04
    that would otherwise look like a renewal.
    """
    if lease.get("moveOutDate"):
        return True
    if lease.get("leaseStatus") in ("Former", "Cancelled", "Evicted", "Denied"):
        return True
    return False


def has_pending_notice(lease: dict) -> bool:
    """
    ResMan's `leaseStatus` field lags — it stays "Current" even after a resident
    submits their notice, only flipping to "Notice to Vacate" at some threshold.
    `noticeToVacateDate` is the authoritative "notice given" signal.
    Return True if notice was given and the resident hasn't actually moved out.
    """
    ntv = lease.get("noticeToVacateDate")
    mo = lease.get("moveOutDate")
    return bool(ntv) and not mo


def is_mtm(lease: dict) -> bool:
    """
    Month-to-month: the lease has effectively expired but the resident is
    still occupying the unit beyond the original end date. Signal:
      - moveOutDate is None (they haven't actually left yet)
      - scheduledMoveOutDate > leaseEndDate (they're staying past original term)

    Examples: EAST-31A1 (end 2026-07-28, scheduled 2026-10-31),
    EAST-29A1 (end 2026-09-05, scheduled 2026-12-19).
    Status in ResMan may still read "Notice to Vacate" even in this state.
    """
    if lease.get("moveOutDate"):
        return False
    sched = parse_iso_date(lease.get("scheduledMoveOutDate", ""))
    end = parse_iso_date(lease.get("leaseEndDate", ""))
    return bool(sched and end and sched > end)


def derive_status(resman_status: str, renewal_entry: dict | None, is_signed: bool,
                  actual_total, offered_total, pending_notice: bool = False,
                  mtm: bool = False) -> str:
    """
    Priority:
      1. Renewal entry present, signed, actual < offered → 'Lower Offer'
      2. Renewal entry present, signed                   → 'Renewed'
      3. Renewal entry present, unsigned, status == 'Pending Renewal' → 'Pending Renewal'
      4. mtm (scheduled move-out past lease end, still here) → 'MTM'
      5. Not renewed AND pending_notice → 'NTV' (overrides stale 'Current')
      6. Else → ResMan status mapped

    MTM overrides NTV because the lease has effectively expired and the
    resident is on month-to-month even if they also gave notice.
    """
    if renewal_entry:
        if is_signed:
            try:
                if offered_total and float(actual_total) < float(offered_total):
                    return "Lower Offer"
            except (TypeError, ValueError):
                pass
            return "Renewed"
        if renewal_entry.get("status") == "Pending Renewal":
            return "Pending Renewal"
        # Unsigned entry with a different status -- fall through to lease-level
    if mtm:
        return "MTM"
    if pending_notice and resman_status == "Current":
        return "NTV"
    return STATUS_MAP.get(resman_status, resman_status)


def run_sync(abbr: str, dry_run: bool = False, limit: int | None = None) -> int:
    """Callable entry point. See main() for CLI wrapper."""
    abbr = abbr.upper()
    tab = tab_name_for(abbr)

    # TRA and AN3 share the same Sheet tab (TRA-AN3) but are separate ResMan
    # properties. Fetch leases from both and merge so all units on the tab match.
    if abbr in ("TRA", "AN3", "TRA-AN3"):
        pids = [(a, get_property_id(a)) for a in ("TRA", "AN3")]
        print(f"[{abbr}] tab '{tab}' <- ResMan merged: " +
              ", ".join(f"{a}={p}" for a, p in pids))
        leases = []
        for a, p in pids:
            if not p:
                print(f"  WARNING: could not resolve ResMan property id for {a}")
                continue
            fetched = fetch_leases(p)
            print(f"  {a}: fetched {len(fetched)} leases")
            leases.extend(fetched)
    else:
        resman_abbr = "49thStr" if abbr == "49THSTR" else abbr
        pid = get_property_id(resman_abbr)
        if not pid:
            print(f"Could not resolve ResMan property id for {resman_abbr}")
            return 2
        print(f"[{abbr}] tab '{tab}' <- ResMan property {resman_abbr} (id={pid})")
        leases = fetch_leases(pid)
    by_unit: dict[str, list] = {}
    for L in leases:
        u = str(L.get("unitNumber", "")).strip()
        if u:
            by_unit.setdefault(u, []).append(L)
    print(f"  fetched {len(leases)} leases across {len(by_unit)} units")

    # Read all sheet columns we need: B Unit, E Resident Name, F Renewal Status, H Exp Date, S Actual Renewal
    header_row = sheets_client.get_range(tab, "A1:AZ1")["values"][0]
    # Sanity-check column positions vs canonical HEADERS
    def col_index(header):
        try:
            return header_row.index(header)
        except ValueError:
            return None

    row_count = sheets_client.inspect_tab(tab)["rowCount"]
    if row_count == 0:
        print("  empty tab, nothing to do")
        return 0
    end_row = row_count + 1  # +1 for header
    data = sheets_client.get_range(tab, f"A2:AZ{end_row}")["values"]

    idx_unit = col_index("Unit")
    idx_name = col_index("Resident Name")
    idx_expmo = col_index("Expiring Month")
    idx_mi = col_index("MI Date")
    idx_status = col_index("Renewal Status")
    idx_offer = col_index("Total Offer A")
    idx_renewal = col_index("Actual Renewal")
    idx_lease_id = col_index("Lease ID")  # may be None if column not added yet
    if idx_renewal is None:
        print(f"  WARNING: no 'Actual Renewal' header found. Headers: {header_row[:22]}")
        return 2
    print(f"  columns: Unit=col{idx_unit+1}, Name=col{idx_name+1}, ExpMonth=col{idx_expmo+1}, "
          f"Status=col{idx_status+1}, TotalOfferA=col{idx_offer+1 if idx_offer is not None else '?'}, "
          f"ActualRenewal=col{idx_renewal+1}, LeaseID=col{idx_lease_id+1 if idx_lease_id is not None else 'none'}")

    by_id = {L.get("leaseId"): L for L in leases if L.get("leaseId")}

    new_status = []
    new_renewal = []
    new_lease_ids = []
    stats = {"renewed": 0, "lower_offer": 0, "pending_renewal": 0, "mtm": 0,
             "current": 0, "moved_out": 0, "ntv": 0, "eviction": 0,
             "unmatched": 0, "other": 0,
             "matched_by_id": 0, "matched_by_name": 0, "matched_by_mi_date": 0}
    unmatched_rows = []

    limit = limit if limit else len(data)
    for i, row in enumerate(data[:limit], start=2):
        row = list(row) + [""] * (max(len(HEADERS), (idx_lease_id or 0) + 1) - len(row))
        unit = str(row[idx_unit]).strip()
        csv_name = str(row[idx_name]).strip()
        expmo_str = str(row[idx_expmo]).strip()
        ym = parse_expiring_month(expmo_str)
        offered_total = row[idx_offer] if idx_offer is not None else ""
        existing_lease_id = str(row[idx_lease_id]).strip() if idx_lease_id is not None else ""

        # Prefer Lease ID if present. Fall back to any-person name matching,
        # then MI-date match (handles name changes like Dawn Lewis → Dawn Nadeau).
        # Try unit as-is, then zero-padded 3 digits (AP has '72' in sheet, '072' in ResMan).
        lease = None
        if existing_lease_id and existing_lease_id in by_id:
            lease = by_id[existing_lease_id]
            stats["matched_by_id"] += 1
            lease_id_out = existing_lease_id
        else:
            unit_variants = [unit]
            if unit.isdigit() and 1 <= len(unit) <= 2:
                unit_variants.append(unit.zfill(3))
            candidates = []
            for u in unit_variants:
                candidates.extend(by_unit.get(u, []))
            lease, sim, _matched_person = choose_lease_for_row(candidates, csv_name)
            if lease is not None:
                stats["matched_by_name"] += 1
                lease_id_out = lease.get("leaseId", "")
            else:
                # Fallback: match by unit + MI Date (name-change / data-anomaly case)
                sheet_mi = parse_sheet_date(str(row[idx_mi])) if idx_mi is not None else None
                lease = find_by_move_in_date(candidates, sheet_mi)
                if lease is not None:
                    stats["matched_by_mi_date"] += 1
                    lease_id_out = lease.get("leaseId", "")
                else:
                    lease_id_out = ""

        if lease is None:
            new_status.append([""])
            new_renewal.append([""])
            new_lease_ids.append([lease_id_out])
            stats["unmatched"] += 1
            unmatched_rows.append((i, unit, csv_name,
                                   [f"{_full_name(p)}({p.get('moveOutDate','')})"
                                    for L in by_unit.get(unit, []) for p in L.get("people", [])]))
            continue

        rm_status = lease.get("leaseStatus", "")
        expiring_entry, renewal_entry = (None, None)
        if ym:
            expiring_entry, renewal_entry = find_expiring_and_renewal(lease, ym[0], ym[1])
        # Trump stale-renewal history entries when the tenancy actually ended.
        live_renewal = renewal_entry if renewal_entry is not None and not lease_has_ended(lease) else None
        is_signed = bool(live_renewal and live_renewal.get("signedDate"))
        actual_total = renewal_total(live_renewal) if is_signed else None
        pending_notice = has_pending_notice(lease)
        mtm = is_mtm(lease)
        status_label = derive_status(rm_status, live_renewal, is_signed,
                                     actual_total, offered_total, pending_notice, mtm)
        new_status.append([status_label])
        new_lease_ids.append([lease_id_out])

        # Col T (Actual Renewal) only filled for signed renewals.
        if status_label in ("Renewed", "Lower Offer"):
            new_renewal.append([actual_total if actual_total is not None else ""])
        else:
            new_renewal.append([""])

        if status_label == "Renewed":            stats["renewed"] += 1
        elif status_label == "Lower Offer":      stats["lower_offer"] += 1
        elif status_label == "Pending Renewal":  stats["pending_renewal"] += 1
        elif status_label == "MTM":              stats["mtm"] += 1
        elif status_label == "Current":          stats["current"] += 1
        elif status_label == "Moved Out":        stats["moved_out"] += 1
        elif status_label == "NTV":              stats["ntv"] += 1
        elif status_label in ("Under Eviction", "Evicted"): stats["eviction"] += 1
        else:                                    stats["other"] += 1

    print()
    print(f"  Summary over {len(data[:limit])} rows:")
    for k, v in stats.items():
        print(f"    {k:<12} {v}")

    if unmatched_rows:
        print(f"\n  Unmatched rows ({len(unmatched_rows)}):")
        for r, u, n, others in unmatched_rows[:15]:
            print(f"    row {r:>3} unit={u:<6} sheet_name={n!r:<35} resman_options={others}")

    if dry_run:
        print("\n  DRY RUN — no writes. Sample of first 12 rows:")
        for i, (s, sr, lid) in enumerate(zip(new_status[:12], new_renewal[:12], new_lease_ids[:12]), start=2):
            row = data[i-2]
            unit = str(row[idx_unit])
            name = str(row[idx_name])
            print(f"    row {i:>3} unit={unit:<6} {name[:25]:<25}  F={s[0]!r:<15}  S={sr[0]!r:<10}  ID={lid[0][:8]!r}")
        return 0

    def a1_col(idx: int) -> str:
        # Handles up to column ZZ. idx is 0-based.
        s = ""
        n = idx
        while True:
            s = chr(ord("A") + n % 26) + s
            n = n // 26 - 1
            if n < 0:
                break
        return s

    col_f = a1_col(idx_status)
    col_s = a1_col(idx_renewal)
    end = 1 + len(new_status)
    print(f"\n  writing {col_f}2:{col_f}{end} (F Renewal Status) and {col_s}2:{col_s}{end} (S Actual Renewal)")
    sheets_client.set_range(tab, f"{col_f}2:{col_f}{end}", new_status, mode="values")
    sheets_client.set_range(tab, f"{col_s}2:{col_s}{end}", new_renewal, mode="values")
    if idx_lease_id is not None:
        col_lid = a1_col(idx_lease_id)
        print(f"  writing {col_lid}2:{col_lid}{end} (Lease ID)")
        sheets_client.set_range(tab, f"{col_lid}2:{col_lid}{end}", new_lease_ids, mode="values")
    print("Done.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("abbr", help="Property abbreviation, e.g. 49THSTR")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="Process only first N rows (for testing)")
    args = ap.parse_args()
    return run_sync(args.abbr, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
