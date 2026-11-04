# SNS Daily Report Dashboard

Real-time property management dashboard for SNS portfolio using ResMan API and Google Sheets integration.

**Live Dashboard:** [https://sns-daily-report.vercel.app](https://sns-daily-report.vercel.app)

**Last Updated:** November 4, 2026

## Quick Start

**View Live:**
Visit [sns-daily-report.vercel.app](https://sns-daily-report.vercel.app)

**Local Development:**
```bash
cd "C:\Users\ymosh\Claude\ROI\SNS Daily Report"
python generate_dashboard.py
```
Then open `dashboard_live.html` in a browser.

---

## Overview

This dashboard provides a unified view of:
- **Unit Status** - Occupancy, vacancies, NTV, evictions across 9 properties
- **Traffic Metrics** - Lead generation, applicants, and lease signing progress
- **Expiring Leases** - 4-month forecast with renewal tracking
- **Renovation Status** - Renovated / Partial / Classic breakdown per property
- **Financial Summary** - Income, credits, net revenue, and delinquency tracking

### Companion pages

- **`/renewals`** — standalone renewals analytics page. See the [Renewals Dashboard section](#renewals-dashboard) below.
- **`/api/record_snapshot`** — daily snapshot endpoint. See the [Supabase Daily Snapshot section](#supabase-daily-snapshot) below.
- **`/api/skill`** — read-only skill markdown for sharing with any AI. See [Supabase Daily Snapshot section](#supabase-daily-snapshot) below.

## Files

| File | Description |
|------|-------------|
| `generate_dashboard.py` | Main script - fetches data and generates HTML |
| `dashboard_live.html` | Generated dashboard (open in browser) |
| `SNS_STYLEGUIDE.md` | SNS design system documentation |
| `DEVELOPMENT.md` | Technical documentation and API reference |
| `.github/workflows/refresh.yml` | GitHub Actions - auto-refresh every 4 hours |
| `api/index.py` | Vercel serverless function - triggers manual refresh |
| `api/get_leases.py` | Vercel API - fetch leases for renewal letters |
| `api/get_charges.py` | Vercel API - fetch deposit and recurring charges |
| `api/push_monday.py` | Vercel API - push data to Monday.com |
| `renewal_summary.json` | Current-month renewal buckets + % per property (regenerated every 4h, consumed by n8n) |
| `vercel.json` | Vercel deployment configuration |
| `requirements.txt` | Python dependencies |
| `renewal_letters.html` | Renewal letter generation tool |
| `renewals.html` | **Renewals Dashboard** — `/renewals` page (see section below) |
| `renewals_data.json` | Baked JSON that powers `renewals.html` (committed by `sync-renewals.yml`) |
| `renewals_sync/` | Python scripts that pull ResMan → Sheet and Sheet → `renewals_data.json` |
| `api/get_renewals.py` | Live fallback that reads the Google Sheet directly (used mid-refresh for immediate feedback) |
| `api/trigger_renewals_sync.py` | Dispatches `sync-renewals.yml` from the Refresh button |
| `api/sync_status.py` | Reports the last `sync-renewals.yml` run status |
| `api/last_sync.py` | Reports the last successful run time (shown in the chip) |
| `.github/workflows/sync-renewals.yml` | GitHub Actions workflow for the Renewals Dashboard refresh pipeline |
| `SNS-letter-logo.jpg` | Self-hosted SNS logo used in the per-resident renewal letter preview |
| `dev_server.py` | Local Python dev server that emulates Vercel routing (static files + `/api/*.py` handlers) |
| `supabase_sync.py` | Maps `data.json` to one row per property; upserts to Supabase REST. CLI + importable. |
| `backfill_snapshots.py` | Parses historical `SNS Daily M_D_YYYY.xlsx` files and upserts with `source='backfill'` |
| `query_snapshots.py` | Read-only inspection of Supabase: row counts, portfolio trend, per-property deltas |
| `api/record_snapshot.py` | **Vercel POST endpoint** — n8n calls this once/day at midnight ET. Fetches data.json, guards against duplicates, inserts 9 rows. |
| `api/skill.py` | **Vercel GET endpoint** — serves the SNS Data Analyst Skill markdown with publishable key baked in. One URL to share with any AI. |
| `skills/SNS Data Analyst Skill.md` | Read-only reference any AI can load to query the Supabase daily snapshots |
| `supabase/migrations/*.sql` | DDL: table + two views + RLS + security_invoker + property_name column |
| `backfill/` | Drop historical xlsx files here for `backfill_snapshots.py` |

---

## Data Sources

### ResMan Legacy API
- **Endpoint**: `https://api.myresman.com`
- **Auth**: POST with JSON body (`IntegrationPartnerID`, `APIKey`, `AccountID`)
- **Endpoints Used**:
  - `Account/GetProperties` - List all properties
  - `Property/GetUnitStatus` - Unit vacancy & ready status (VacancyStatus, Status fields)
  - `Leasing/GetApplicantsAndCurrentResidents` - Identify eviction units

### ResMan Partners API
- **Endpoint**: `https://partners-api.myresman.com`
- **Auth**: GET with Basic Auth (PartnerID:APIKey) + Header `ResMan-Account-Id`
- **Timeout**: 30 seconds with 2 retries (3-second delay between retries)
- **Endpoints Used**:
  - `GET /Leasing/Leases` - Lease data with history for renewals
  - `GET /Units/Availability` - Unit vacancy, occupancy, move-out dates (primary endpoint)
  - `GET /Units` - Unit details, renovation status, `availableForOnlineMarketing` field
  - `GET /WorkOrders` - Open work orders and new orders in last 24h
  - `GET /Transactions/Receivables` - Owed account balances as of a date (used for **Receivable MTD** / **Del %**)

### Google Sheets (Traffic)
- **Sheet ID**: `1D8eBjdcClYf9L4DNQg2cKDE1Wuwkc5AOSQcRCPmz18A`
- **Tab**: Traffic (gid=343109953)
- **Access**: Public (view only)

### Google Sheets (Financial)
- **Sheet ID**: `1TF3cpVlGcllKMJnkPOB_fHmFgN-wwb1qbiJ05l8JMNU`
- **Tab**: transactions (gid=0) - Income charges and credits
- **Tab**: Del (gid=763378106) - Delinquency data (Current, Under Eviction, Formers)
- **Access**: Public (view only)
- **Source**: n8n automation from ResMan

---

## Properties (9)

| Region | Properties |
|--------|------------|
| Tampa | 49thStr |
| Orlando | TV, MOC, MUSO |
| Jacksonville | AP, SJ, ROP, ORT |
| Ft. Lauderdale | EAST |

**Note:** Down/Abated units are excluded from all counts and calculations.

---

## Unit Status Logic

### VacancyStatus Values (from Legacy API)
The `Property/GetUnitStatus` endpoint returns these VacancyStatus values:
- `Occupied` - Unit is occupied
- `Vacant` - Unit is vacant, not pre-leased
- `Vacant Pre-Leased` - Unit is vacant but has a signed future lease
- `Notice to Vacate` - Current resident gave notice, not pre-leased
- `Notice to Vacate Pre-Leased` - Current resident gave notice, has future lease signed

### Dashboard Categories

| Category | Logic | Notes |
|----------|-------|-------|
| **Occupied** | VacancyStatus = "Occupied" | |
| **Vacant Ready** | VacancyStatus in ["Vacant", "Vacant Pre-Leased"] AND Status = "Ready" | Includes pre-leased |
| **Vacant Not Ready** | VacancyStatus in ["Vacant", "Vacant Pre-Leased"] AND Status != "Ready" | Includes pre-leased |
| **Vacant Leased** | VacancyStatus = "Vacant Pre-Leased" | Subset of Vacant Ready/Not Ready |
| **NTV** | VacancyStatus in ["Notice to Vacate", "Notice to Vacate Pre-Leased"] | Includes pre-leased |
| **NTV Leased** | VacancyStatus = "Notice to Vacate Pre-Leased" | Subset of NTV |
| **Under Eviction** | Resident Status = "Under Eviction" (from GetApplicantsAndCurrentResidents) | |

### Calculated Metrics

#### Rentable Units
```
Rentable Units = Vacant Ready + Vacant Not Ready + NTV - Vacant Leased - NTV Leased
```
This gives the count of **non-pre-leased** vacant and NTV units that need to be rented.

**Example (ORT):**
- Vacant Ready: 25
- Vacant Not Ready: 9
- NTV: 23
- Vacant Leased: 6
- NTV Leased: 0
- **Rentable Units = 25 + 9 + 23 - 6 - 0 = 51**

#### Off the Market
Units that are rentable but NOT listed for online marketing.

**Source:** Partners API `/Units` endpoint, field `availableForOnlineMarketing`

**Logic:**
1. Get all units from Partners API with `availableForOnlineMarketing` field
2. Filter to only non-pre-leased units (VacancyStatus in ["Vacant", "Notice to Vacate"])
3. Exclude Down/Abated units
4. Exclude Under Eviction units
5. Count units where `availableForOnlineMarketing = false`

**Example (ORT):**
- Rentable Units: 51
- Off the Market: 27
- Available for Marketing: 24 (implied: 51 - 27)

#### Physical Occupancy
```
Physical Occupancy % = (Total - Vacant Ready - Vacant Not Ready) / Total * 100
```

#### Occupancy Rented
```
Occupancy Rented % = (Total - Vacant Ready - Vacant Not Ready + Vacant Leased) / Total * 100
```
Includes pre-leased vacant units as "rented".

---

## Dashboard Sections

### 1. Portfolio Summary Table
| Metric | Description |
|--------|-------------|
| Total Units | All active units per property (excludes Down/Abated) |
| Occupied | Units with "Occupied" status |
| Vacant Ready | Vacant + ready (includes pre-leased) |
| Vacant Not Ready | Vacant + not ready (includes pre-leased) |
| NTV | Notice to Vacate (includes NTV Leased) |
| Under Eviction | From resident status |
| **Physical Occupancy** | `(Total - Vacant Ready - Vacant Not Ready) / Total` |
| Vacant Leased | Subset: pre-leased units in vacant counts |
| NTV Leased | Subset: pre-leased units in NTV count |
| **Rentable Units** | `Vacant Ready + Vacant Not Ready + NTV - Vacant Leased - NTV Leased` |
| Off the Market | Rentable units not listed online |
| **Occupancy Rented** | `Physical Occupancy + Vacant Leased` |

### 2. Traffic Summary Table
| Metric | Source | Description |
|--------|--------|-------------|
| Total Prospects | Google Sheets | MTD leads per property |
| Daily AVG | Google Sheets | MTD prospects ÷ calendar days elapsed this month (zero-lead days included) |
| **Total Applicants** | Partners API | Pending leases (future move-ins) |
| Pending Approval | Partners API | `approvalDate` is null |
| Approved | Partners API | Has `approvalDate`, no `leaseSignedDate` |
| Signed Lease | Partners API | Has `leaseSignedDate` |

### 3. Expiring Leases Table
Shows leases expiring in current month + next 3 months (4 total).

**Collapsible months** - Click to expand/collapse each month.

**Header shows Renewal %** per property:
- Formula: `Renewed / (Current + Renewed + NTV + Under Eviction)`
- Excludes "Pending Renewal" (undecided)

| Status | Description |
|--------|-------------|
| Current | No action taken yet |
| Pending Renewal | Renewal offered, not signed |
| Renewed | Renewal signed |
| NTV | Notice to Vacate given |
| Under Eviction | Eviction in progress |

**Color coding:**
- Green: >= 70% renewal
- Amber: 50-69% renewal
- Red: < 50% renewal

### 4. Renovation Status Table
Shows unit renovation breakdown per property.

| Status | Description |
|--------|-------------|
| Renovated | Fully renovated units (parsed from `unitType` field) |
| Partial Renovated | Partially renovated units |
| Classic | Original/non-renovated units |

**Data source:** Partners API `GET /Units` - parses `unitType` field

### 5. Financial Summary Table
Shows income and delinquency data per property.

**Income Section:**
| Metric | Description |
|--------|-------------|
| Total Income | Sum of all charges for the period |
| Total Credit | Sum of all credits/adjustments |
| Net | Total Income - Total Credit |
| Receivable MTD | Owed to the property **as of today** — sum of positive account balances (ResMan Partners API `GET /Transactions/Receivables`) |
| Del % | Receivable MTD ÷ Net × 100, color-coded (>10% red, >5% amber, else green) |

**Delinquency Section:**
| Metric | Description |
|--------|-------------|
| Current | Amount owed by current residents (hover for count) |
| Under Eviction | Amount owed by residents under eviction (hover for count) |
| Formers | Amount owed by former residents (hover for count) |
| Total | Sum of all delinquency categories (hover for count) |

**Data source:** Google Sheets (populated by n8n from ResMan)

**Features:**
- Hover over delinquency cells to see resident count in tooltip
- Grey divider separates Income and Delinquency sections
- Region-grouped columns matching other tables

---

## Features

- **Live hosting** on Vercel with auto-refresh every 4 hours
- **Manual refresh** button with loading animation and auto-reload
- **Mobile responsive** - works on phones and tablets
- **Dark/light mode** toggle (persists in browser)
- **Pop-up tables** - Click any metric to see detailed unit/lease list
- **Sortable columns** - Click column headers to sort ascending/descending
- **Clickable links** - Unit numbers and resident names link directly to ResMan
- **Status filters** - Filter applicants and expiring leases by status
- **Alert indicators** - Highlights cells with date issues (past available/move-in/move-out dates)
- Region-grouped columns (Tampa, Orlando, Jacksonville, Ft. Lauderdale)
- Tooltip explanations on metrics (hover over ?)
- Color-coded occupancy rates (green 95%+, amber 90-95%, red <90%)
- Collapsible expiring leases with renewal % preview
- Work orders summary per property (open count, new in 24h)
- Responsive tables with horizontal scroll on mobile
- Florida timezone (ET) for all timestamps

---

## Recent Changes

### November 4, 2026
- **Shipped:** Supabase daily-snapshot pipeline. One row per property per calendar date (ET) in `property_snapshots`, plus `portfolio_snapshots` and `property_daily_deltas` views. See [Supabase Daily Snapshot](#supabase-daily-snapshot) section below.
- **Added:** `/api/record_snapshot` — single POST endpoint called by n8n daily, does guard + insert + returns `success`/`already_recorded`/`error` status.
- **Added:** `/api/skill` — serves a read-only analyst skill at one URL. Publishable (anon) key embedded; RLS + `security_invoker=on` restrict it to SELECT.
- **Added:** `property_name` column on `property_snapshots` so queries surface the full name (e.g. "The Village at San Jose"), not just the abbreviation.
- **Backfilled:** 2026-10-01 → 2026-11-03 from the historical xlsx bundle (306 rows across 34 unique dates × 9 properties).
- **Hardening:** `supabase_sync.py` mapper emits `NULL` for `prospects_mtd` / `daily_avg_prospects` / `conversion_rate_pct` when the traffic source is absent (prevents a repeat of the 10/22-10/24 CRM-dark incident).

### September 29, 2026
- **Added:** `renewal_summary.json` at repo root — machine-readable current-month renewal snapshot for n8n end-of-month email. Contains per-property buckets (Current / Pending Renewal / Renewed / NTV-Formers / Under Eviction), computed `renewalPct` (same formula as the dashboard header: `renewed / (current + renewed + ntvFormers + underEviction)`), and a `portfolio` total row. Regenerated + committed on every 4h refresh alongside `dashboard_live.html`. Consume via `https://raw.githubusercontent.com/ymi-flowing/sns-daily-report/main/renewal_summary.json` with a GitHub PAT (repo is private).

### May 26, 2026
- **Added:** ResMan prospect traffic for **AN3 & TRA** — these two properties are not in the CRM Google Sheet, so their Total Prospects / Daily AVG now come from the Partners API `GET /Prospects` (MTD count by first-contact date, including Lost).
- **Changed:** Traffic **Daily AVG** for all properties is now MTD prospects ÷ calendar days elapsed in the month (zero-lead days included), replacing the old divide-by-distinct-lead-days method.
- **Added:** **Receivable MTD** and **Del %** rows in the Financial Summary card (under Net), for all 12 properties. Receivable MTD = today's owed balances from Partners API `GET /Transactions/Receivables`; Del % = Receivable MTD ÷ Net.

### May 19, 2026
- **Fixed:** API reliability improvements for ResMan Partners API
  - Added retry logic (2 retries with 3-second delay)
  - Added timeout handling (30 seconds per request)
  - Added delay between API calls to avoid rate limiting
  - Process smaller properties first to improve success rate
- **Optimized:** Migrated to `Units/Availability` endpoint (replaces legacy GetUnitStatus)
- **Added:** Graceful degradation when API is unavailable

### May 6, 2026
- **Added:** Financial Summary card with income and delinquency data
  - Total Income, Total Credit, Net revenue per property
  - Current, Under Eviction, Formers delinquency amounts
  - Hover tooltips showing resident count on delinquency cells
  - Data sourced from Google Sheets (populated by n8n automation)
- **Added:** Property name mapping (full names to abbreviations)
- **UI:** Optimized column widths across all tables (120px status column)
- **UI:** Consistent text sizing across all dashboard cards
- **Styling:** Added top-center tooltip style for financial data

### May 5, 2026
- **Added:** Alert system for date issues:
  - Vacant Not Ready: highlights units with past available date
  - Vacant Leased: highlights units with past move-in date
  - NTV: highlights units with past move-out date or insufficient turnaround time
  - Light pink background in light mode, light blue in dark mode
  - Red dot with tooltip in pop-up rows showing specific alert reason
- **Added:** Pop-up tables - click any metric to see detailed unit/lease list
- **Added:** Sortable columns in pop-ups (click headers to sort)
- **Added:** Clickable unit numbers - link directly to ResMan unit page
- **Added:** Clickable resident names - link directly to ResMan resident page
- **Added:** Status filters for Applicants and Expiring Leases pop-ups
- **Added:** Work orders summary per property (open count, new in 24h)
- **Added:** Resident names in Future MI, Future MO, and Expiring Leases
- **Added:** Custom scrollbar styling
- **Fixed:** Date sorting in pop-up tables
- **Cleaned:** Removed archive folder and debug scripts

### April 28, 2026
- **Deployed:** Live dashboard on Vercel with auto-refresh every 4 hours
- **Added:** Manual refresh button with loading overlay and auto-reload
- **Added:** Mobile responsive design (works on phones/tablets)
- **Added:** Dark/light mode toggle
- **Fixed:** JSON serialization bug (`eviction_units` set -> list)
- **Fixed:** EAST off-market count (was showing 23, now correctly shows 14)
- **Renamed:** "Available for Rent" -> "Rentable Units"
- **UI:** Streamlined timestamp display (single location in header)
- **UI:** Hidden property dropdown (pending individual property pages)

---

## Deployment

### Current Setup (Live)
- **Hosting:** Vercel (https://sns-daily-report.vercel.app)
- **Repository:** GitHub (https://github.com/ymi-flowing/sns-daily-report)
- **Auto-refresh:** GitHub Actions runs every 4 hours
- **Manual refresh:** Click refresh button on dashboard (triggers GitHub Actions via Vercel API)

### How It Works
1. GitHub Actions runs `generate_dashboard.py` on schedule or manual trigger
2. Script fetches fresh data from ResMan API and Google Sheets
3. Generates new `dashboard_live.html` with current timestamp
4. Commits and pushes to GitHub
5. Vercel auto-deploys on push

### Environment Variables (GitHub Secrets)
- `RESMAN_API_KEY` - ResMan API key
- `RESMAN_PARTNER_ID` - ResMan partner ID
- `RESMAN_ACCOUNT_ID` - ResMan account ID

### Environment Variables (Vercel)
- `GITHUB_TOKEN` - Fine-grained token with Actions write permission (for refresh button)

---

## Next Steps

- [x] ~~Lease expiration tracking~~
- [x] ~~Renovation status tracking~~
- [x] ~~Off the Market tracking~~
- [x] ~~Fix off-market calculation~~
- [x] ~~Deploy to live hosting~~
- [x] ~~Mobile responsive design~~
- [x] ~~Work order metrics~~
- [x] ~~Pop-up tables with detailed data~~
- [x] ~~Financial Summary card~~ - Income, Credits, Net revenue per property
- [x] ~~Delinquency metrics~~ - Current, Under Eviction, Formers tracking
- [ ] Individual property detail pages
- [ ] Historical trends and charts
- [ ] Automated daily email reports

---

## Supabase Daily Snapshot

Every property's daily state is persisted in Supabase so trends can be analysed over time. Single endpoint drives it; one skill file lets any AI answer analytical questions over the data.

### Pipeline

```
ResMan + Google Sheets  (sources, every 4h)
        |
        v
generate_dashboard.py   writes data.json + dashboard_live.html (unchanged)
        |
        v
n8n (midnight ET)       calls POST /api/record_snapshot once a day
        |
        v
/api/record_snapshot    fetch data.json -> guard against duplicates -> insert 9 rows
        |
        v
Supabase property_snapshots  one row per (property, date), kept forever
```

### Database objects

| Object | Role |
|---|---|
| `property_snapshots` (table) | One row per `(property_abbr, snapshot_date)`. ~70 columns. |
| `portfolio_snapshots` (view) | Portfolio totals summed per `snapshot_date`. |
| `property_daily_deltas` (view) | Day-over-day deltas for MTD counters; window partitioned by month so deltas reset on the 1st automatically. |

**Security:** RLS enabled on the base table. Publishable (`sb_publishable_*`) key can `SELECT`, cannot write. Secret (`sb_secret_*`) key used only by the Vercel endpoint for inserts. Both views set to `security_invoker=on` so they honour the base table's RLS.

### Three defenses against duplicate daily records

1. `/api/record_snapshot` pre-flight SELECT: returns `already_recorded` without writing.
2. `UNIQUE(property_abbr, snapshot_date)` constraint on the table.
3. `Prefer: resolution=ignore-duplicates` on the POST → `ON CONFLICT DO NOTHING`.

### Endpoint contract

**`POST /api/record_snapshot`** (no body, no auth)

Returns one of three shapes:

```json
{ "status": "success",           "snapshot_date": "YYYY-MM-DD", "inserted": 9 }
{ "status": "already_recorded",  "snapshot_date": "YYYY-MM-DD", "existing_count": 9 }
{ "status": "error",             "snapshot_date": "YYYY-MM-DD", "error": "..." }
```

`error` cases also return a non-2xx HTTP code so n8n's error branch picks them up cleanly.

### Environment variables (Vercel, Production)

- `SUPABASE_URL` — `https://judbcwsdalgfwaxzhxlq.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — the `sb_secret_*` key with write access

### The analyst skill

**`GET /api/skill`** serves `skills/SNS Data Analyst Skill.md` as `text/markdown` with a 60-second cache. The file is self-contained:

- Supabase URL + publishable key (read-only)
- Full field dictionary for all 3 DB objects
- PostgREST filter cheatsheet
- Nine example queries + Python/curl/JavaScript code samples
- "Finding trends and valuable data points" playbook with 4 lenses, baseline-comparison table, 13 signals-to-flag thresholds, 5 workflow recipes
- NULL-semantics + MTD-reset gotchas so the AI reads the data correctly

Share the URL with any AI that supports URL fetching (Claude Desktop, Cursor, Claude Code, ChatGPT with browsing). No file management, no stale copies.

### Known data-quality notes (encoded in the skill)

- `applicants_mtd` family NULL for `snapshot_date < 2026-10-28` — old xlsx layout didn't carry those columns.
- `generated_at` and `future_mi_count` NULL for all historical rows (backfill artifact).
- `applicants_mtd_active` *decreases* within a month (not strictly cumulative — applicants exit when decided).
- `prospects_mtd = NULL` means the CRM source was dark (don't confuse with "zero leads"). `0` means literal zero activity.
- Mid-month `income_total` / `credit_total` downticks are usually real ResMan adjustments, not pipeline bugs.

### Local tooling

```bash
# Dry-run the mapper against today's data.json
python supabase_sync.py --dry-run

# Backfill historical xlsx files (after dropping them into backfill/)
python backfill_snapshots.py --dir backfill --dry-run
python backfill_snapshots.py --dir backfill --apply

# Inspect what landed
python query_snapshots.py --prop AP
```

Needs `.env.local` with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`.

---

## Renewals Dashboard

Standalone page at **`/renewals`** that surfaces every expiring lease plus signed-renewal detail, with per-resident PDF letter preview. Same Vercel app, static-first load, same off-white light / dark theme.

### Load pattern

```
Normal open:
  browser → GET /renewals_data.json → ~100ms, no API

Refresh click:
  POST /api/trigger_renewals_sync
    → GitHub Actions sync-renewals.yml:
        1. renewals_sync/sync_all.py            ResMan → Sheet
        2. renewals_sync/write_renewals_json.py Sheet + ResMan dates → renewals_data.json
        3. git commit + push                    bakes JSON into repo
    → dashboard polls /api/sync_status until "completed"
    → dashboard reads /api/get_renewals (live sheet) for immediate feedback
    → next normal reload serves the newly-baked static JSON
```

ETA ~3–5 minutes end-to-end.

### Secrets required (GitHub Actions AND Vercel env vars)

- `RESMAN_API_KEY`, `RESMAN_PARTNER_ID`, `RESMAN_ACCOUNT_ID` — ResMan Partners API
- `SHEETS_WEBHOOK_URL` — Apps Script `/exec` endpoint for the Renewal Letters sheet
- `SHARED_SECRET` — Apps Script shared secret
- `GITHUB_TOKEN` — for `/api/trigger_renewals_sync` to dispatch the workflow

### Status vocabulary (col G in the Google Sheet)

Written by `renewals_sync/sync_renewal_status.py`:

| Status | Meaning |
|---|---|
| `Renewed` | Signed renewal entry in `lease.history[]`, actual ≥ offered |
| `Lower Offer` | Signed renewal, actual < offered |
| `Pending Renewal` | History entry with `status == "Pending Renewal"` AND `signedDate` null |
| `Current` | No renewal entry yet; still live, no notice |
| `MTM` | `scheduledMoveOutDate > leaseEndDate` AND no `moveOutDate` yet (month-to-month) |
| `NTV` | `noticeToVacateDate` set, no `moveOutDate` yet |
| `Moved Out`, `Under Eviction`, `Evicted`, `Cancelled`, `Denied` | Mapped straight from `lease.leaseStatus` |

The "best renewal candidate" picker handles the common ResMan case of a Cancelled sibling sitting next to a signed Current renewal with the same `startDate`: it ranks `(signed AND not cancelled) > signed > not cancelled > latest applicationDate`.

### Dashboard sections

1. **Header** — logo + "Renewals Dashboard" + Renewal Offers button + Last Updated chip + Refresh + theme toggle.
2. **Filter bar** — five cascading multi-selects (Property, Status, Expiring Month, Unit Type, Floorplan) + Clear all. Each dropdown's options are narrowed by the other active filters.
3. **8 KPI cards** — Total Expiring, Renewal Rate, Lower Offer %, Avg Renewed Total, Pending Decision, Avg Offered Δ, Avg Actual Δ, Avg Expiring/Month. Each has a short `?` tooltip (just the calculation). Double-click opens a drill-in modal.
4. **Status × Expiring Month** summary — 9 status rows + Total + three rate rows:
   - `Renewed of total` = (Renewed + Lower Offer) / Total
   - `% Renewable` = (Pending Renewal + Current) / Total
   - `Avg Δ (signed)` = mean of Actual Δ $ and % across signed renewals
   Every cell is clickable → drill-in modal.
5. **4 charts** —
   - Status breakdown by month (stacked bar)
   - Renewal rate by month (stacked bar, bar height = signed/total)
   - Renewed (actual) Delta % distribution (cyan histogram, signed only)
   - Avg renewed total by month (cyan line; hover shows Avg Renewed, Avg Δ $, Avg Δ %)
6. **Expiring leases table** — the detailed roster. Property + Unit + Status frozen left. Resident name links to ResMan's resident detail page. NTV Date and Move Out Date cells show a green ✓ when the rule applies. Two chip filters in the card header: **Insufficient Notice**, **Early Termination** — both filter the whole report (KPIs, charts, summary table, offers popup).

### Renewal Offers popup

Opens from the "Renewal Offers" header button. Reads the **same filtered subset** as the main dashboard but shows an offer-focused grid:

Property · Unit · Lease End (all frozen) · Resident · Current Rent · Current Total · Rent Δ · Total Δ · Rent Offer · Utility · Valet · Pest · Amenity · Internet · Total Utilities · Pet · Parking · WD · Storage · Total Other · Total Offer

A checkbox column + select-all header lets the user pick specific residents. **"Print Letters"** button prints all selected (or all visible if none selected) by opening a single new tab with every letter stacked and separated by `page-break-before: always` — one Ctrl+P produces a multi-page PDF. Totals footer sums every $ column (deltas sum too).

Each resident name is clickable → opens that resident's formal renewal letter in a new tab (A4, print-ready). The letter template is inlined into `renewals.html` as `LETTER_TEMPLATE`; placeholder data is produced by `buildLetterContext()`, which ports `letters/generate_letters.py` logic (first-name split, charges rows, deposit sub-block). The SNS logo is self-hosted at `/SNS-letter-logo.jpg`.

### `renewals_sync/` directory

Copies of the upstream Renewal Letters pipeline kept in-repo so the GitHub Actions workflow can run without external deps:

| File | Role |
|---|---|
| `sync_all.py` | Loops through 10 properties → calls `sync_renewal_status.run_sync()` on each |
| `sync_renewal_status.py` | ResMan → Sheet: writes Renewal Status, Actual Renewal, Lease ID |
| `create_monday_items.py` | ResMan API helpers (`fetch_leases`, `get_property_id`) — Monday code dead but harmless |
| `sheets_client.py` | Apps Script webhook wrapper |
| `sheet_schema.py` | Canonical headers + tab names |
| `write_renewals_json.py` | **NEW** — fetches each sheet tab, joins ResMan lease data by Lease ID, writes `renewals_data.json` with four extra columns (NTV Date, Move Out Date (falls back to scheduled), Lease End Date, Renewal Signed Date) |

**Important:** `renewals_sync/sync_renewal_status.py` is a hand-maintained copy of the one in `C:\Users\ymosh\Claude\ROI\Renewal Letters`. When fixing a status-detection bug, mirror the fix to both.

### Styling notes

- Light mode body is `bg-slate-100` (not slate-50); cards + modals + tooltips use `#fafbfc` via a scoped `html:not(.dark) .bg-white { … !important }` override. Dark mode uses the normal `dark:bg-slate-800` for cards.
- Scrollbars are slate (grey-cyan), not blue. Both `.fancy-scroll` and the global `*` scrollbar use `#94a3b8 → #64748b` light / `#64748b → #475569` dark.
- Every checkbox-looking control (dropdowns, Offers rows, Insufficient Notice / Early Termination chips) shares one themed style: border-only unchecked, ✓ in light-gray (`#64748b` light / `#94a3b8` dark). No native `accent-color`.
- `#06b6d4` cyan is the brand accent on this page: MTM pill, chart 3 bars, chart 4 line, filter count badge, Renewal Offers button hover.

### Local development

```bash
# Static files + /api/*.py handlers, mimics Vercel routing
python dev_server.py
# → http://localhost:3000/renewals
```

Needs `.env.local` with `SHEETS_WEBHOOK_URL` and `SHARED_SECRET` (plus `RESMAN_API_KEY`/`PARTNER_ID`/`ACCOUNT_ID` if testing `write_renewals_json.py` locally).

---

*Generated with ResMan API + Google Sheets integration*
