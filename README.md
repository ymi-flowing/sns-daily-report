# SNS Daily Report Dashboard

Real-time property management dashboard for SNS portfolio using ResMan API and Google Sheets integration.

**Live Dashboard:** [https://sns-daily-report.vercel.app](https://sns-daily-report.vercel.app)

**Last Updated:** May 26, 2026

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
- **Unit Status** - Occupancy, vacancies, NTV, evictions across 12 properties
- **Traffic Metrics** - Lead generation, applicants, and lease signing progress
- **Expiring Leases** - 4-month forecast with renewal tracking
- **Renovation Status** - Renovated / Partial / Classic breakdown per property
- **Financial Summary** - Income, credits, net revenue, and delinquency tracking

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
| `vercel.json` | Vercel deployment configuration |
| `requirements.txt` | Python dependencies |
| `renewal_letters.html` | Renewal letter generation tool |

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
  - `GET /Prospects` - MTD prospects by first-contact date (used for **AN3 & TRA** traffic, which are not in the CRM sheet)
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

## Properties (12)

| Region | Properties |
|--------|------------|
| Tampa | 49thStr |
| Orlando | MP, TV, MOC, MUSO |
| Jacksonville | AP, SJ, ROP, ORT |
| Ft. Lauderdale | EAST, TRA, AN3 |

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
| Total Prospects | Google Sheets¹ | MTD leads per property |
| Daily AVG | Google Sheets¹ | MTD prospects ÷ calendar days elapsed this month (zero-lead days included) |
| **Total Applicants** | Partners API | Pending leases (future move-ins) |
| Pending Approval | Partners API | `approvalDate` is null |
| Approved | Partners API | Has `approvalDate`, no `leaseSignedDate` |
| Signed Lease | Partners API | Has `leaseSignedDate` |

¹ **AN3 & TRA** are not in the CRM Google Sheet — their Total Prospects / Daily AVG come from the ResMan Partners API `GET /Prospects` (prospects with first contact since the 1st of the month, **including Lost**).

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

*Generated with ResMan API + Google Sheets integration*
