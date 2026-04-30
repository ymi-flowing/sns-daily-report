# SNS Daily Report Dashboard

Real-time property management dashboard for SNS portfolio using ResMan API and Google Sheets integration.

**Live Dashboard:** [https://sns-daily-report.vercel.app](https://sns-daily-report.vercel.app)

**Last Updated:** April 30, 2026

## Quick Start

**View Live:**
Visit [sns-daily-report.vercel.app](https://sns-daily-report.vercel.app)

**Local Development:**
```bash
cd "C:\Users\ymosh\ROI\SNS Daily Report"
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

## Files

| File | Description |
|------|-------------|
| `generate_dashboard.py` | Main script - fetches data and generates HTML |
| `dashboard_live.html` | Generated dashboard (open in browser) |
| `SNS_STYLEGUIDE.md` | SNS design system documentation |
| `Cardentials.txt` | API credentials (keep secure, in .gitignore) |
| `archive/` | Exploration scripts, debug tools, raw data from development |
| `.github/workflows/refresh.yml` | GitHub Actions - auto-refresh every 4 hours |
| `api/index.py` | Vercel serverless function - triggers manual refresh |
| `vercel.json` | Vercel deployment configuration |
| `requirements.txt` | Python dependencies |

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
- **Endpoints Used**:
  - `GET /Leasing/Leases` - Lease data with history for renewals
  - `GET /Units` - Unit details, renovation status, `availableForOnlineMarketing` field

### Google Sheets (Traffic)
- **Sheet ID**: `1D8eBjdcClYf9L4DNQg2cKDE1Wuwkc5AOSQcRCPmz18A`
- **Tab**: Traffic (gid=343109953)
- **Access**: Public (view only)

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
| Total Prospects | Google Sheets | MTD leads per property |
| Daily AVG | Google Sheets | Average leads per day |
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

---

## Features

- **Live hosting** on Vercel with auto-refresh every 4 hours
- **Manual refresh** button with loading animation and auto-reload
- **Mobile responsive** - works on phones and tablets
- **Dark/light mode** toggle (persists in browser)
- Region-grouped columns (Tampa, Orlando, Jacksonville, Ft. Lauderdale)
- Tooltip explanations on metrics (hover over ?)
- Color-coded occupancy rates (green 95%+, amber 90-95%, red <90%)
- Collapsible expiring leases with renewal % preview
- Responsive tables with horizontal scroll on mobile
- Florida timezone (ET) for all timestamps

---

## Recent Changes

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
- [ ] **Financial Summary card** - Charges, Credits, Income (via n8n + Google Sheet)
- [ ] **Delinquency metrics** - Payment tracking and aged receivables
- [ ] **Work order metrics** - Maintenance requests and completion rates
- [ ] Individual property detail pages
- [ ] Historical trends and charts
- [ ] Automated daily email reports
- [ ] API optimization - reduce duplicate calls (see DEVELOPMENT.md)

---

*Generated with ResMan API + Google Sheets integration*
