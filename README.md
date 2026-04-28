# SNS Daily Report Dashboard

Real-time property management dashboard for SNS portfolio using ResMan API and Google Sheets integration.

**Last Updated:** April 28, 2026

## Quick Start

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
| `Cardentials.txt` | API credentials (keep secure) |
| `archive/` | Exploration scripts, debug tools, and raw data from development |

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

- Dark/light mode toggle
- Property filter dropdown
- Region-grouped columns (Tampa, Orlando, Jacksonville, Ft. Lauderdale)
- Tooltip explanations on metrics (hover over ?)
- Color-coded occupancy rates (green 95%+, amber 90-95%, red <90%)
- Collapsible expiring leases with renewal % preview
- Responsive tables with sticky first column
- Clickable property headers to filter

---

## Recent Changes

### April 28, 2026
- **Fixed:** JSON serialization bug (`eviction_units` set -> list)
- **Fixed:** EAST off-market count (was showing 23, now correctly shows 14)
- **Renamed:** "Available for Rent" -> "Rentable Units"
- **Verified:** Off-market calculation matches ResMan system

---

## Deployment Options (Future)

1. **SharePoint/OneDrive** - Simple file sharing
2. **GitHub Pages** - Free static hosting
3. **Internal Web Server (IIS)** - Private, auto-updates via Task Scheduler
4. **Azure Static Web Apps** - Cloud, private, scalable
5. **Python Web Server** - Local, live data refresh

---

## Next Steps

- [x] ~~Lease expiration tracking~~
- [x] ~~Renovation status tracking~~
- [x] ~~Off the Market tracking~~
- [x] ~~Fix off-market calculation~~
- [ ] **Rent collection metrics** - How much rent was collected this month
- [ ] Payment tracking and delinquency
- [ ] Work order / maintenance metrics
- [ ] Historical trends and charts
- [ ] Automated daily email reports
- [ ] Deploy to live hosting

---

*Generated with ResMan API + Google Sheets integration*
