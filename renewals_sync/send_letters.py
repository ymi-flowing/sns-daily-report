"""
Send Renewal Offer Letters - ResMan
Built for 49th St Apartments (October 2026 run), portable to other properties.

For each expiring lease shown on the Expiring Leases report:
  - Open the resident detail page
  - Click the mailto: email link (opens the in-app Send Email dialog)
  - Change From to the property's pm@... mailbox (auto-detects, works for any property)
  - Apply template '***Renewals - Renewal Offer'
  - Tick every CC lease-holder whose email differs from the primary To
  - Attach the unit's PDF via Add -> Add from computer
  - Click Send

SAFETY
  Default flow is non-interactive: it sends every matched unit in sequence.
  Progress is written to progress-{tag}.json so a mid-run failure is
  resumable -- reruns skip units that already show status=SENT.
  Use --dry-run to stage only the first unit (no Send) and eyeball the
  dialog before authorizing a real send.

RUN
    python send_letters.py                # send all matched units, headed
    python send_letters.py --dry-run      # stage first unit only, halt before Send, then exit
    python send_letters.py --headless     # invisible browser
    python send_letters.py --restart      # ignore progress file, re-check every unit
"""
import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Frame


HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
# Vendored into SNS Daily Report: sheets_client lives next to this file.
sys.path.insert(0, str(HERE))
import sheets_client  # noqa: E402  (import after sys.path shim)


# Property abbr -> sheet tab name. TRA + AN3 share tab.
SHEET_TAB = {
    "49THSTR": "49thStr",
    "TRA":     "TRA-AN3",
    "AN3":     "TRA-AN3",
}

# Sheet rows with these Renewal Status get a letter sent.
LETTER_STATUSES = {"Current", "NTV"}

# ---- property + month resolution (parameterized 2026-09-10) ----
#
# `python send_letters.py --property 49THSTR --month 2026-12` resolves all
# per-property/per-month config below. Bare `python send_letters.py` still
# works via DEFAULTS (last-used config); useful for one-off reruns.

DEFAULT_PROPERTY = "49THSTR"
DEFAULT_MONTH    = "2026-12"

# Filename prefix per property. Must match FILE_PREFIX in letters/generate_letters.py.
FILE_PREFIX = {
    "49THSTR": "49th",
    "ORT":     "The Villas",
    "AP":      "Arbor Place",
    "TV":      "The Vista",
    "MOC":     "The Morgan",
    "MUSO":    "The Mustang",
    "SJ":      "San Jose",
    "ROP":     "Riverside",
    "EAST":    "Eastpointe",
    "TRA":     "The River",
    "AN3":     "AN3",
}

# Formal folder name per property (must match generate_letters.py output path).
PROPERTY_FOLDER = {
    "49THSTR": "49th St Apartments",
    "ORT":     "The Villas at Ortega",
    "AP":      "Arbor Place Apartments",
    "TV":      "The Vista At Winter Park",
    "MOC":     "The Morgan At Ocala Apartments",
    "MUSO":    "The Mustang at Ocala",
    "SJ":      "The Village at San Jose",
    "ROP":     "Riverside at Orange Park",
    "EAST":    "The Village at Eastpointe Apartments",
    "TRA":     "The River Apartments",
    "AN3":     "AN3 Apartments",
}

# EXACT option text in the ResMan Expiring Leases → Properties Chosen dropdown.
# Case + trailing whitespace matter for the JS lookup. See _probe_properties_field.py.
# NOTE: AN3 in ResMan is "AN3 Investments" (differs from letter footer's "AN3 Apartments").
RESMAN_PROPERTY_NAME = {
    "49THSTR": "49th St Apartments",
    "ORT":     "The Villas at Ortega",
    "AP":      "Arbor Place Apartments",
    "TV":      "The Vista At Winter Park",
    "MOC":     "The Morgan At Ocala Apartments",
    "MUSO":    "The Mustang at Ocala",
    "SJ":      "The Village at San Jose",
    "ROP":     "Riverside at Orange Park",
    "EAST":    "The Village at Eastpointe Apartments",
    "TRA":     "The River Apartments",
    "AN3":     "AN3 Investments",
}


def resolve_config(abbr: str, month: str, pdf_root: Path | None = None) -> dict:
    """Given ABBR + YYYY-MM, return the derived config dict.

    When vendored into SNS Daily Report, the GH Actions workflow passes
    pdf_root pointing to the directory where generate_letters.py emitted its
    PDFs for this run (default is still PROJECT_ROOT/Generated Letters).
    """
    dt = datetime.strptime(month, "%Y-%m")
    month_folder = dt.strftime("%b %Y")        # e.g. "Dec 2026"
    month_label  = dt.strftime("%B %Y")        # e.g. "December 2026"
    month_tag    = dt.strftime("%b-%Y").lower()  # e.g. "dec-2026"

    prefix = FILE_PREFIX.get(abbr)
    if not prefix:
        raise ValueError(
            f"No FILE_PREFIX for {abbr!r}. Add it to send_letters.py before running."
        )
    folder = PROPERTY_FOLDER.get(abbr)
    if not folder:
        raise ValueError(
            f"No PROPERTY_FOLDER for {abbr!r}. Add it to send_letters.py before running."
        )
    resman_name = RESMAN_PROPERTY_NAME.get(abbr)
    if not resman_name:
        raise ValueError(
            f"No RESMAN_PROPERTY_NAME for {abbr!r}. Add it to send_letters.py before running."
        )

    pdf_root = pdf_root or (PROJECT_ROOT / "Generated Letters")
    pdf_dir = pdf_root / folder / month_folder
    # Escape the prefix for regex (handles spaces & special chars).
    prefix_re = re.escape(prefix)
    month_re = re.escape(month_folder)
    # Unit numbers vary by property: pure digits (49th, AP, MOC, MUSO, ROP),
    # alphanumeric (ORT="C007", SJ="1655BU", EAST="03A1"), hyphenated (TV="4700-5",
    # TRA="510-1"). Broadened regex covers all shapes.
    pdf_pattern = re.compile(
        rf"{prefix_re}\s*-\s*{month_re}\s*-\s*([A-Za-z0-9-]+)\.pdf", re.IGNORECASE
    )
    tag = f"{prefix.lower().replace(' ', '')}-{month_tag}"
    return {
        "abbr":         abbr,
        "month":        month,
        "month_label":  month_label,
        "month_tag":    tag,
        "resman_name":  resman_name,
        "pdf_dir":      pdf_dir,
        "pdf_pattern":  pdf_pattern,
        "progress_path": HERE / f"progress-{tag}.json",
        "log_csv_path":  HERE / f"sent-log-{tag}.csv",
    }


# Lease statuses to include (in addition to Current, which is on by default).
# Leaves Former + Evicted unchecked.
EXTRA_LEASE_STATUSES = [
    "Month to Month",
    "Notice to Vacate",
    "Under Eviction",
    "Renewed",
    "Pending Renewal",
]

REPORT_URL     = "https://sns.myresman.com/Reports/ExpiringLeases"
USERNAME       = "SNS_Assistant"
PASSWORD       = "SNSassistant123$"

TEMPLATE_LABEL = "***Renewals - Renewal Offer"


# ---------- login + report parameter dialog ----------

def login(page: Page) -> None:
    page.goto(REPORT_URL, wait_until="domcontentloaded")
    if "Account/Login" in page.url:
        page.locator("#Username").fill(USERNAME)
        page.locator("#Password").fill(PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(re.compile(r"https://sns\.myresman\.com/"), timeout=30000)
    page.wait_for_load_state("domcontentloaded")
    print("Logged in.", flush=True)


def set_properties_filter(page: Page, target_name: str) -> None:
    """
    Set the Expiring Leases Properties multi-select (jQuery Chosen widget) so
    ONLY target_name is selected. Direct DOM manipulation on the underlying
    <select>, then trigger Chosen's 'chosen:updated' to re-render the UI.

    Underlying element: <select id="PropertyOrGroupParameter_PropertyOrGroupIDs"
    multiple>. Chosen wrapper: #PropertyOrGroupParameter_PropertyOrGroupIDs_chosen.
    """
    result = page.evaluate(r"""
      (target) => {
        const sel = document.querySelector('#PropertyOrGroupParameter_PropertyOrGroupIDs');
        if (!sel) return {ok: false, error: 'select not found'};
        let matched = null;
        const available = [];
        for (const opt of sel.querySelectorAll('option')) {
          const text = (opt.textContent || '').trim();
          available.push(text);
          const shouldSelect = text === target;
          opt.selected = shouldSelect;
          if (shouldSelect) matched = { value: opt.value, text };
        }
        if (!matched) return {ok: false, error: 'no option matched: ' + target, available};
        if (window.jQuery) {
          window.jQuery(sel).trigger('chosen:updated').trigger('change');
        } else {
          sel.dispatchEvent(new Event('change', {bubbles: true}));
        }
        return {ok: true, matched};
      }
    """, target_name)

    if not result.get("ok"):
        raise RuntimeError(
            f"set_properties_filter({target_name!r}) failed: {result.get('error')}\n"
            f"Available options: {result.get('available')}"
        )
    print(f"  Properties -> {result['matched']['text']!r} (value={result['matched']['value']})",
          flush=True)

    # Verify UI reflects the change: the Chosen chip list should contain the target.
    try:
        chip_texts = page.evaluate(r"""
          () => [...document.querySelectorAll(
                   '#PropertyOrGroupParameter_PropertyOrGroupIDs_chosen li.search-choice span'
                 )]
                .map(e => (e.textContent || '').trim())
        """)
    except Exception:
        chip_texts = []
    if target_name not in chip_texts:
        print(f"  WARN: Chosen chip list is {chip_texts!r} (expected {target_name!r})",
              flush=True)


def open_report_with_parameters(page: Page, month_label: str, resman_name: str) -> Frame:
    """Fill the Expiring Leases parameter dialog and open the report."""
    if "Reports/ExpiringLeases" not in page.url:
        page.goto(REPORT_URL, wait_until="domcontentloaded")

    # Wait for the parameter dialog
    period_id = "PeriodDateRangeOrPeriodRangeParameter_AccountingPeriodIDInput"
    page.wait_for_selector(f"#{period_id}", timeout=20000)

    # Ensure Properties is scoped to just the target property (else the
    # persisted last-selected property is used — leads to "0 matched" or,
    # if unit numbers collide across properties, wrong sends).
    set_properties_filter(page, resman_name)
    # Open the Kendo listbox via the chevron button (naming convention: <InputID>Button)
    period_button = page.locator(f"#{period_id}Button")
    if period_button.count() > 0:
        period_button.click()
    else:
        # Fallback: click the input's sibling button, or the input itself + ArrowDown
        sibling_btn = page.locator(f"#{period_id}").locator("xpath=following-sibling::button").first
        if sibling_btn.count() > 0:
            sibling_btn.click()
        else:
            page.locator(f"#{period_id}").click()
            page.locator(f"#{period_id}").press("Alt+ArrowDown")

    # Give Kendo a moment to render its listbox items.
    page.wait_for_timeout(500)

    # ResMan uses Kendo listbox items — role="option" (not "menuitem" as older
    # UI versions did). Try a chain of selectors so this survives future UI
    # tweaks. First match wins.
    candidates = [
        page.get_by_role("option",   name=month_label, exact=True),
        page.get_by_role("menuitem", name=month_label, exact=True),
        page.locator("li.k-list-item, li.k-item").filter(has_text=month_label),
        page.get_by_text(month_label, exact=True),
    ]
    picked = None
    for loc in candidates:
        try:
            loc.first.wait_for(timeout=5000)
            picked = loc.first
            break
        except PWTimeout:
            continue

    if picked is None:
        try:
            visible = page.evaluate(r"""
              () => [...document.querySelectorAll(
                       '[role="option"], [role="menuitem"], li.k-list-item, li.k-item'
                     )]
                     .map(e => (e.textContent || '').trim())
                     .filter(t => t.length > 0)
                     .slice(0, 80)
            """)
        except Exception:
            visible = []
        ss = HERE / f"debug-period-dropdown-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        try:
            page.screenshot(path=str(ss), full_page=True)
        except Exception:
            pass
        raise RuntimeError(
            f"Period item {month_label!r} not found (tried option/menuitem/k-item/text).\n"
            f"Visible items ({len(visible)}): {visible}\n"
            f"Screenshot: {ss}"
        )
    picked.click()

    # Lease Statuses dropdown (button labelled "N selected")
    page.get_by_role("button", name=re.compile(r"\d+ selected")).click()
    for status in EXTRA_LEASE_STATUSES:
        cb = page.get_by_role("checkbox", name=status)
        # jQuery may already have it checked from a previous session — leave alone
        if not cb.is_checked():
            cb.click()
    # Close the statuses dropdown by clicking the dialog title area
    page.get_by_text("Expiring Leases", exact=True).first.click()

    # Hide MTM and Expired Sections
    hide_cb = page.get_by_role("checkbox", name="Hide MTM and Expired Sections")
    if not hide_cb.is_checked():
        hide_cb.click()

    # OK — scoped to the parameter dialog to avoid hitting an OK in some other popup
    page.get_by_role("dialog", name="Expiring Leases").get_by_role("button", name="OK").click()

    # Wait for the parameter dialog to close (confirms the OK was accepted)
    try:
        page.wait_for_selector(
            f"#{period_id}", state="hidden", timeout=10000
        )
    except PWTimeout:
        # Sometimes the input goes hidden without being removed; try again
        page.wait_for_timeout(1000)

    # Wait for the iframe element to be attached before checking its content
    try:
        page.wait_for_selector(
            'iframe[name="ReportViewer_ContentFrame"]',
            state="attached",
            timeout=30000,
        )
    except PWTimeout:
        print("WARN: report iframe did not attach in 30s", flush=True)

    # Wait for the report iframe to load with content
    return _wait_for_report_iframe(page)


def _wait_for_report_iframe(page: Page, timeout_s: int = 240) -> Frame:
    """Poll for the ReportViewer iframe to contain at least one Residents/Detail link."""
    print(f"Waiting for report to render (up to {timeout_s}s)...", flush=True)
    deadline = time.time() + timeout_s
    last_diag = 0.0
    while time.time() < deadline:
        for fr in page.frames:
            if fr.name != "ReportViewer_ContentFrame":
                continue
            try:
                has_data = fr.evaluate(
                    r"""() => {
                      const cells = document.querySelectorAll('td[onmousedown*="Residents/Detail/"]');
                      return cells.length > 0;
                    }"""
                )
            except Exception:
                has_data = False
            if has_data:
                print(f"Report iframe ready ({fr.name}).", flush=True)
                return fr
        if time.time() - last_diag > 15:
            frames = [(fr.name, (fr.url or "")[:80]) for fr in page.frames]
            print(f"    still waiting ({int(deadline - time.time())}s left). frames={frames}", flush=True)
            last_diag = time.time()
        time.sleep(1.0)
    try:
        ss = HERE / f"debug-report-timeout-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        page.screenshot(path=str(ss), full_page=True)
        print(f"Failure screenshot: {ss}", flush=True)
    except Exception:
        pass
    raise RuntimeError("Timed out waiting for the Expiring Leases report to render.")


def scrape_roster(frame: Frame) -> list[dict]:
    """Walk report cells with onmousedown='...Residents/Detail/<guid>...'.
    Each such cell is a resident name; find the unit number in the same row.
    Return [{unit, name, lease_id}, ...] with duplicates removed.
    """
    rows = frame.evaluate(r"""
      () => {
        const out = [];
        const cells = document.querySelectorAll('td[onmousedown*="Residents/Detail/"]');
        for (const td of cells) {
          const m = (td.getAttribute('onmousedown') || '').match(/Residents\/Detail\/([a-f0-9-]{16,})/i);
          if (!m) continue;
          const name = (td.textContent || '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
          if (!name) continue;
          const row = td.closest('tr');
          const rowTexts = row ? [...row.querySelectorAll('td')].map(t =>
              (t.textContent || '').replace(/ /g,' ').replace(/\s+/g,' ').trim()
          ) : [];
          let unit = '';
          for (const t of rowTexts) {
            if (!t || t === name) continue;
            // Unit shape varies by property: pure digits (49th, AP, MOC, MUSO,
            // ROP), alphanumeric (ORT="C007", SJ="1655BU", EAST="03A1"),
            // hyphenated (TV="4700-5", TRA="510-1"). Must contain at least one
            // digit so we don't match column headers or property names.
            if (/^[A-Za-z0-9-]{2,10}$/.test(t) && /[0-9]/.test(t)) { unit = t; break; }
          }
          out.push({unit, name, lease_id: m[1]});
        }
        return out;
      }
    """)
    seen = set()
    roster = []
    for r in rows:
        if r["lease_id"] in seen:
            continue
        seen.add(r["lease_id"])
        roster.append(r)
    return roster


# ---------- PDF matching ----------

def scan_pdfs(pdf_dir: Path, pdf_pattern: re.Pattern) -> dict[str, Path]:
    """Return {unit_key: PDF path}. For short numeric unit numbers, also
    register the zero-padded 3-digit form so we match ResMan's roster which
    pads to 3 digits (e.g. sheet '8' <-> ResMan '008')."""
    out = {}
    if not pdf_dir.exists():
        return out
    for p in sorted(pdf_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        m = pdf_pattern.search(p.name)
        if not m:
            continue
        unit = m.group(1)
        out[unit] = p
        if unit.isdigit() and 1 <= len(unit) <= 2:
            out[unit.zfill(3)] = p
    return out


# ---------- progress ----------

def load_progress(progress_path: Path) -> dict:
    if progress_path.exists():
        return json.loads(progress_path.read_text())
    return {}


def save_progress(progress_path: Path, progress: dict) -> None:
    progress_path.write_text(json.dumps(progress, indent=2))


# ---------- streaming status for the dashboard ----------
#
# The GH Actions workflow passes --status-file + --run-id. After every
# unit (SENT, STAGED, FAIL), we rewrite that file atomically so the
# dashboard can poll it as a static asset. Separate from the per-property
# progress-{tag}.json (which is for resumability on reruns).

def _read_status(status_path: Path) -> dict:
    if status_path.exists():
        try:
            return json.loads(status_path.read_text())
        except Exception:
            pass
    return {}


def _write_status(status_path: Path, status: dict) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp.write_text(json.dumps(status, indent=2, default=str))
    tmp.replace(status_path)


def status_init(status_path: Path, run_id: str, total: int, dry_run: bool) -> None:
    _write_status(status_path, {
        "runId": run_id,
        "startedAt": datetime.now().isoformat(timespec="seconds"),
        "mode": "dry-run" if dry_run else "live",
        "status": "running",
        "total": total,
        "sent": [],
        "staged": [],
        "failed": [],
        "completedAt": None,
    })


def status_record(status_path: Path, cfg: dict, result: dict) -> None:
    s = _read_status(status_path)
    entry = {
        "property": cfg["abbr"],
        "month": cfg["month"],
        "unit": result.get("unit"),
        "resident": result.get("name"),
        "status": result.get("status"),
        "detail": result.get("detail", ""),
        "attachment": result.get("attachment", ""),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    bucket = {"SENT": "sent", "STAGED": "staged"}.get(entry["status"], "failed")
    s.setdefault(bucket, []).append(entry)
    _write_status(status_path, s)


def status_finalize(status_path: Path) -> None:
    s = _read_status(status_path)
    s["status"] = "complete"
    s["completedAt"] = datetime.now().isoformat(timespec="seconds")
    _write_status(status_path, s)


def close_send_email_dialog(page: Page) -> None:
    """Dismiss any open Send Email dialog so the next unit starts clean.
    Dry-run leaves the dialog open; we need to close it between iterations.
    """
    try:
        # Primary strategy: click the X on the top-right of the dialog chrome.
        close_btn = page.locator("div.k-dialog span.k-dialog-close, div.k-window span.k-i-close").first
        if close_btn.count() > 0 and close_btn.is_visible():
            close_btn.click()
            page.wait_for_timeout(400)
            return
        # Fallback: press Escape until no modal remains.
        for _ in range(3):
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            if page.locator("div.k-dialog, div.k-window").count() == 0:
                return
    except Exception as e:
        print(f"  WARN: couldn't close dialog cleanly: {e}", flush=True)


def append_log(log_csv_path: Path, row: dict) -> None:
    new = not log_csv_path.exists()
    with log_csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


# ---------- send-email dialog steps ----------

def _dismiss_lingering_modals(page: Page) -> None:
    """Close any Send Email / Add From Computer dialog left open by a previous
    failure. The detail URL is a hash route so page.goto doesn't tear these down.
    """
    for _ in range(3):
        dismissed = False
        for name in ("Add From Computer", "Send Email"):
            btn = page.get_by_role("dialog", name=name).get_by_role("button", name="Cancel")
            if btn.count() == 0:
                continue
            try:
                btn.first.click(timeout=1500)
                dismissed = True
                page.wait_for_timeout(300)
            except Exception:
                pass
        if not dismissed:
            break


def open_send_email_dialog(page: Page, lease_id: str) -> None:
    _dismiss_lingering_modals(page)
    page.goto(
        f"https://sns.myresman.com/#/Residents/Detail/{lease_id}",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector('a[href^="mailto:"]', timeout=15000)
    page.locator('a[href^="mailto:"]').first.click()
    page.wait_for_selector("#FromObjectInputButton", timeout=15000)


def get_primary_to_email(page: Page) -> str:
    """Read the primary To email from the dialog. The visible text looks like
    'Name <email@x>'. Try #PrimaryEmailAddress first, then fall back to text.
    """
    return page.evaluate(r"""
      () => {
        const el = document.querySelector('#PrimaryEmailAddress');
        if (el && el.value) return el.value.trim();
        // Text-based fallback: find the To cell
        const rows = document.querySelectorAll('table tr');
        for (const r of rows) {
          const tds = r.querySelectorAll('td');
          if (tds.length >= 2 && (tds[0].textContent || '').trim() === 'To') {
            const t = (tds[1].textContent || '').trim();
            const m = t.match(/<([^>]+)>/);
            return m ? m[1].trim() : t;
          }
        }
        return '';
      }
    """) or ""


def set_from_pm(page: Page) -> str:
    """Open the From dropdown and pick the menuitem containing 'pm@'."""
    page.locator("#FromObjectInputButton").click()
    # Kendo/jQuery menuitems -- must use real Playwright click
    pm_item = page.get_by_role("menuitem").filter(has_text="pm@")
    pm_item.first.wait_for(timeout=5000)
    label = pm_item.first.inner_text().strip()
    pm_item.first.click()
    val = page.locator("#FromObjectInput").input_value()
    # ORT uses "Pm@" (capital P) — accept any case.
    if "pm@" not in val.lower():
        raise RuntimeError(f"From did not switch to pm@ mailbox; got {val!r}")
    return label


def apply_template(page: Page) -> None:
    page.locator("#GetTemplates").click()
    # There can be two copies of the same link (data-id duplicated); pick the visible one.
    link = page.locator(f'a.emailTemplate:visible:has-text("{TEMPLATE_LABEL}")').first
    link.wait_for(timeout=10000)
    link.click()
    page.wait_for_function(
        "() => document.querySelector('#Subject') && document.querySelector('#Subject').value.length > 5",
        timeout=10000,
    )


def check_cc_skipping_duplicates(page: Page, primary_email: str) -> list[str]:
    """Tick every CC lease-holder EXCEPT those whose email matches the primary To."""
    primary_lc = (primary_email or "").strip().lower()
    cc_emails: list[str] = []
    count = page.evaluate(
        "() => document.querySelectorAll('input[name^=\"CCPeople\"][name$=\".SendEmail\"]').length"
    )
    for i in range(count):
        cb = page.locator(f"#CCPeople_{i}__SendEmail")
        if cb.count() == 0:
            continue
        email = page.locator(f"#CCPeople_{i}__EmailAddress").input_value().strip()
        if email and email.lower() == primary_lc:
            print(f"    skip CC (same email as primary): {email}", flush=True)
            continue
        if not cb.is_checked():
            cb.check()
        if email:
            cc_emails.append(email)
    return cc_emails


def attach_pdf(page: Page, pdf_path: Path) -> None:
    # Click the Attachments Add button (id="Add") to reveal the small dropdown
    # (containing "Add from ResMan" + "Add from computer"), then click the latter.
    add_btn = page.locator("#Add")
    add_btn.click()
    afc = page.locator("#btnAddFromComputer")
    try:
        afc.wait_for(state="visible", timeout=3000)
    except PWTimeout:
        # Rare: menu didn't reveal on click. Hover once, then try again.
        add_btn.hover()
        afc.wait_for(state="visible", timeout=3000)
    afc.click()

    # Add From Computer modal: click the Choose File button, feed the file via chooser
    with page.expect_file_chooser() as fc_info:
        page.get_by_role("button", name="Choose File").click()
    fc_info.value.set_files(str(pdf_path))
    page.wait_for_timeout(400)

    # Click the modal's OK. The Send Email dialog also has buttons, so scope by
    # the Add-From-Computer dialog role.
    ok_btn = page.get_by_role("dialog", name="Add From Computer").get_by_role("button", name="OK")
    ok_btn.click()

    # Confirm the attachment shows in the Send Email dialog
    page.wait_for_function(
        f"() => [...document.querySelectorAll('li')].some(li => li.textContent.includes('{pdf_path.name}'))",
        timeout=10000,
    )


def read_dialog_state(page: Page) -> dict:
    return page.evaluate(r"""
      () => {
        const from = document.querySelector('#FromObjectInput')?.value || '';
        const subject = document.querySelector('#Subject')?.value || '';
        const attachments = [...document.querySelectorAll('ul li')]
                    .map(li => li.textContent.trim())
                    .filter(t => t.toLowerCase().endsWith('.pdf'));
        // To text
        let to = '';
        for (const r of document.querySelectorAll('table tr')) {
          const tds = r.querySelectorAll('td');
          if (tds.length >= 2 && (tds[0].textContent || '').trim() === 'To') {
            to = (tds[1].textContent || '').trim();
            break;
          }
        }
        return {to, from, subject, attachments};
      }
    """)


def click_send(page: Page) -> None:
    """Click Send inside the Send Email dialog (Cancel button also has role=button)."""
    dlg = page.get_by_role("dialog", name="Send Email")
    dlg.get_by_role("button", name="Send").click()
    # Dialog closes when Send is accepted
    page.wait_for_function(
        "() => !document.querySelector('#FromObjectInputButton')",
        timeout=20000,
    )


# ---------- per-resident orchestration ----------

def process_one(page: Page, entry: dict, pdf_path: Path, dry_run: bool) -> dict:
    t0 = time.time()
    result = {
        "unit": entry["unit"],
        "name": entry["name"],
        "lease_id": entry["lease_id"],
        "attachment": pdf_path.name,
        "cc": "",
        "sent_at": "",
        "status": "",
        "detail": "",
        "seconds": 0,
    }
    try:
        open_send_email_dialog(page, entry["lease_id"])
        primary_email = get_primary_to_email(page)
        set_from_pm(page)
        apply_template(page)
        cc_list = check_cc_skipping_duplicates(page, primary_email)
        attach_pdf(page, pdf_path)
        state = read_dialog_state(page)

        result["cc"] = "; ".join(cc_list)
        print(
            f"  to={primary_email!r}\n"
            f"  from={state['from']!r}\n"
            f"  subj={state['subject']!r}\n"
            f"  cc  ={cc_list}\n"
            f"  attached={state['attachments']}",
            flush=True,
        )

        if dry_run:
            result["status"] = "STAGED"
            result["detail"] = "dry-run, Send not clicked"
            return result

        click_send(page)
        result["status"] = "SENT"
        result["sent_at"] = datetime.now().isoformat(timespec="seconds")
    except Exception as ex:
        result["status"] = "FAIL"
        result["detail"] = str(ex)[:400]
    finally:
        result["seconds"] = round(time.time() - t0, 1)
    return result


# ---------- sheet-based roster (direct-URL flow) ----------

def _get_range_with_retry(tab: str, range_a1: str, attempts: int = 4) -> dict:
    """Retry get_range on transient Apps Script 404s (safe: read-only)."""
    last = None
    for i in range(attempts):
        try:
            return sheets_client.get_range(tab, range_a1)
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(2 ** i)
                continue
    raise last


def build_roster_from_sheet(abbr: str, month: str) -> tuple[list[dict], dict]:
    """
    Read the property tab + filter to (Expiring Month == month, Status Current,
    has Lease ID). Return (roster, warnings) where roster is a list of
    {unit, name, lease_id} dicts. Direct-URL flow: no ResMan report needed.
    """
    dt = datetime.strptime(month, "%Y-%m")
    wanted_iso = f"{dt.year:04d}-{dt.month:02d}"
    wanted_text = dt.strftime("%B %Y")

    tab = SHEET_TAB.get(abbr, abbr)
    data = _get_range_with_retry(tab, "A1:AJ500")
    values = data.get("values", [])
    if not values:
        return [], {"empty_tab": True}
    headers = values[0]

    def col(name):
        try:
            return headers.index(name)
        except ValueError:
            return None

    i_month  = col("Expiring Month")
    i_unit   = col("Unit")
    i_name   = col("Resident Name")
    i_status = col("Renewal Status")
    i_lease  = col("Lease ID")
    missing = [n for n, i in [("Expiring Month", i_month), ("Unit", i_unit),
                              ("Resident Name", i_name), ("Renewal Status", i_status),
                              ("Lease ID", i_lease)] if i is None]
    if missing:
        raise RuntimeError(f"Tab {tab!r} missing required columns: {missing}")

    roster: list[dict] = []
    skipped_status: dict[str, int] = {}
    missing_lease_id: list[dict] = []
    for row in values[1:]:
        if not row or not str(row[i_month] if i_month < len(row) else "").strip():
            continue
        em = str(row[i_month]).strip()
        if not (em.startswith(wanted_iso) or em == wanted_text):
            continue
        status = str(row[i_status] if i_status < len(row) else "").strip() or "(blank)"
        if status not in LETTER_STATUSES:
            skipped_status[status] = skipped_status.get(status, 0) + 1
            continue
        unit = str(row[i_unit] if i_unit < len(row) else "").strip()
        name = str(row[i_name] if i_name < len(row) else "").strip()
        lease_id = str(row[i_lease] if i_lease < len(row) else "").strip()
        if not lease_id:
            missing_lease_id.append({"unit": unit, "name": name})
            continue
        roster.append({"unit": unit, "name": name, "lease_id": lease_id})

    warnings = {"skipped_status": skipped_status, "missing_lease_id": missing_lease_id}
    return roster, warnings


# ---------- main ----------

def run_one_property(cfg: dict, headless: bool, dry_run: bool, restart: bool,
                     only_units: list[str] | None = None,
                     status_path: Path | None = None) -> int:
    """Run the send flow for one property/month config. Returns exit code."""
    pdfs = scan_pdfs(cfg["pdf_dir"], cfg["pdf_pattern"])
    if not pdfs:
        print(f"No PDFs found in {cfg['pdf_dir']}", flush=True)
        return 1
    _unit_sort_key = lambda u: (0, int(u)) if u.isdigit() else (1, u)
    print(f"Found {len(pdfs)} PDFs. Units: {sorted(pdfs.keys(), key=_unit_sort_key)}",
          flush=True)

    # Build roster from the sheet (direct-URL flow: no ResMan report scrape).
    roster, warnings = build_roster_from_sheet(cfg["abbr"], cfg["month"])
    if only_units:
        wanted = set(only_units)
        before = len(roster)
        roster = [r for r in roster if r["unit"] in wanted]
        print(f"--only-units filter: {before} -> {len(roster)} rows (wanted {sorted(wanted)})",
              flush=True)
    print(f"Sheet roster: {len(roster)} eligible rows for {cfg['abbr']} {cfg['month']}",
          flush=True)
    if warnings.get("skipped_status"):
        print(f"  skipped by status: {warnings['skipped_status']}", flush=True)
    if warnings.get("missing_lease_id"):
        print(f"  WARN: rows missing Lease ID (skipped):", flush=True)
        for r in warnings["missing_lease_id"]:
            print(f"    unit={r['unit']} name={r['name']!r}", flush=True)

    progress = {} if restart else load_progress(cfg["progress_path"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()

        login(page)

        # Match roster units to PDFs (with zero-pad fallback via scan_pdfs).
        matched = []
        unmatched_units = []
        for entry in roster:
            u = entry["unit"]
            if u in pdfs:
                matched.append((entry, pdfs[u]))
            else:
                unmatched_units.append(u)
        pdf_only = sorted(
            set(pdfs) - {e["unit"] for e in roster} - {str(pdfs[k]).lower() for k in pdfs if False},
            key=_unit_sort_key,
        )
        # Re-derive pdf_only more cleanly: PDFs whose keys aren't any roster unit or their variant.
        roster_units = {e["unit"] for e in roster}
        pdf_only = sorted(
            [k for k in pdfs if k not in roster_units and k.lstrip("0") not in roster_units],
            key=_unit_sort_key,
        )
        if pdf_only:
            print(f"WARN: PDFs with no matching sheet row: {pdf_only}", flush=True)
        if unmatched_units:
            print(f"WARN: sheet rows with no matching PDF: {unmatched_units}", flush=True)
        if not matched:
            print("No matched units. Aborting.", flush=True)
            return 1

        todo = [(e, p) for (e, p) in matched
                if progress.get(e["unit"], {}).get("status") != "SENT"]

        already = len(matched) - len(todo)
        print(f"\n{len(matched)} matched, {already} already sent, {len(todo)} to process.",
              flush=True)
        for i, (e, p) in enumerate(todo, 1):
            print(f"  {i:>2}. unit {e['unit']:>4}  {e['name']:<30}  <- {p.name}", flush=True)

        if not todo:
            print("Nothing to do.", flush=True)
            return 0

        # Dry-run w/o a status file keeps the historical "stage first unit,
        # halt for review" behavior (useful locally in a headed browser).
        # With a status file (CI mode), iterate ALL units in dry-run so each
        # one's STAGED state ends up in the status stream.
        if dry_run and status_path is None:
            first_entry, first_pdf = todo[0]
            print(f"\n=== DRY-RUN: staging unit {first_entry['unit']} ({first_entry['name']}) ===",
                  flush=True)
            result = process_one(page, first_entry, first_pdf, dry_run=True)
            print(f"  {result['status']} - {result.get('detail', '')}", flush=True)
            ss = HERE / f"staged-{cfg['month_tag']}-{first_entry['unit']}-{datetime.now().strftime('%H%M%S')}.png"
            try:
                page.screenshot(path=str(ss), full_page=True)
                print(f"  screenshot: {ss}", flush=True)
            except Exception as ex:
                print(f"  screenshot failed: {ex}", flush=True)
            print("\nDRY-RUN complete. Dialog left open ~15 min for review.", flush=True)
            try:
                input("Press Enter to close the browser.")
            except (KeyboardInterrupt, EOFError):
                time.sleep(900)
            browser.close()
            return 0

        results = []
        for i, (entry, pdf_path) in enumerate(todo, 1):
            label = "DRY" if dry_run else "LIVE"
            print(f"\n[{label} {i}/{len(todo)}] unit {entry['unit']}  {entry['name']}", flush=True)
            result = process_one(page, entry, pdf_path, dry_run=dry_run)
            results.append(result)
            append_log(cfg["log_csv_path"], result)
            progress[entry["unit"]] = result
            save_progress(cfg["progress_path"], progress)
            tag = {"SENT": "OK  ", "STAGED": "STG ", "FAIL": "FAIL"}.get(
                result["status"], "FAIL"
            )
            print(f"  -> {tag} {result['status']} ({result['seconds']}s)  {result.get('detail','')}",
                  flush=True)
            if status_path is not None:
                status_record(status_path, cfg, result)
            # In dry-run, the Send dialog stays open after process_one; close
            # it so the next unit's open_send_email_dialog starts clean.
            if dry_run:
                close_send_email_dialog(page)

        ok = sum(1 for r in results if r["status"] == "SENT")
        print(f"\nDone. Sent {ok}/{len(results)}.", flush=True)
        failures = [r for r in results if r["status"] != "SENT"]
        if failures:
            print("Failures:", flush=True)
            for r in failures:
                print(f"  {r['unit']:>4} {r['name']}  -> {r['detail']}", flush=True)

        # Final screenshot for records
        try:
            ss = HERE / f"final-{cfg['month_tag']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
            page.screenshot(path=str(ss), full_page=True)
            print(f"Final screenshot: {ss}", flush=True)
        except Exception:
            pass

        browser.close()
        return 0 if not failures else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--property", default=DEFAULT_PROPERTY,
                    help=f"Property abbr (default: {DEFAULT_PROPERTY})")
    ap.add_argument("--month", default=DEFAULT_MONTH,
                    help=f"Target month YYYY-MM (default: {DEFAULT_MONTH})")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dry-run",  action="store_true",
                    help="Don't click Send. Without --status-file: stage first "
                         "unit + halt for review. With --status-file: iterate "
                         "all matched units in STAGED mode (CI dashboard flow).")
    ap.add_argument("--restart", action="store_true",
                    help="ignore progress file, re-check every unit")
    ap.add_argument("--only-units", nargs="+", default=None,
                    help="Only send to these specific units (space-separated). "
                         "e.g. --only-units 318 515 902")
    ap.add_argument("--pdf-root", default="",
                    help="Override the root dir where PDFs live "
                         "(default: PROJECT_ROOT/Generated Letters)")
    ap.add_argument("--status-file", default="",
                    help="Path to a JSON file to stream per-unit progress into. "
                         "The dashboard polls this file.")
    ap.add_argument("--run-id", default="",
                    help="Tag written into the status file. Required with --status-file.")
    args = ap.parse_args()

    abbr = args.property.upper()
    pdf_root = Path(args.pdf_root).expanduser().resolve() if args.pdf_root else None
    try:
        cfg = resolve_config(abbr, args.month, pdf_root=pdf_root)
    except ValueError as e:
        print(f"ERROR: {e}", flush=True)
        return 2

    status_path = Path(args.status_file).expanduser().resolve() if args.status_file else None
    if status_path and not args.run_id:
        print("ERROR: --run-id is required when --status-file is set", flush=True)
        return 2

    print(f"=== send_letters: {abbr} / {args.month} ===", flush=True)
    print(f"  ResMan prop:  {cfg['resman_name']!r}", flush=True)
    print(f"  PDF dir:      {cfg['pdf_dir']}", flush=True)
    print(f"  PDF pattern:  {cfg['pdf_pattern'].pattern}", flush=True)
    print(f"  Month label:  {cfg['month_label']!r}", flush=True)
    print(f"  Progress:     {cfg['progress_path'].name}", flush=True)
    print(f"  Log:          {cfg['log_csv_path'].name}", flush=True)
    if status_path:
        print(f"  Status file:  {status_path}", flush=True)
    print()

    # When the caller provides a status file, assume they want a run-wide
    # record. Initialize it only if empty — the batch orchestrator may have
    # already created it for a multi-property run.
    if status_path and not status_path.exists():
        total_estimate = len(args.only_units) if args.only_units else 0
        status_init(status_path, args.run_id, total_estimate, args.dry_run)

    rc = run_one_property(
        cfg, args.headless, args.dry_run, args.restart,
        only_units=args.only_units, status_path=status_path,
    )
    if status_path:
        status_finalize(status_path)
    return rc


if __name__ == "__main__":
    sys.exit(main())
