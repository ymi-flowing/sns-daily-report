# SNS Daily Report - Development Notes

## API Configuration

### Current Settings (as of May 26, 2026)

```python
API_TIMEOUT = 30          # Seconds per request
API_MAX_RETRIES = 2       # Number of retries on failure
API_RETRY_DELAY = 3       # Seconds between retries
API_CALL_DELAY = 0.5      # Seconds between individual API calls
PROPERTY_REQUEST_DELAY = 1 # Seconds between properties
```

### Retry Logic
- Each API call has a 30-second timeout
- On timeout or failure, retries up to 2 times with 3-second delay
- Small delay (0.5s) between API calls to avoid rate limiting
- 1 second delay between processing each property

### Property Processing Order
Properties are processed smallest-first to improve API warm-up:
```python
TARGET_PROPERTIES = ["AN3", "TRA", "ROP", "TV", "MP", "SJ", "MUSO", "MOC", "EAST", "ORT", "49thStr", "AP"]
```

---

## API Call Analysis

### Current API Calls Per Dashboard Refresh (After Optimization)

**Total: ~53 API calls** (3 global + 12×4 Partners per-property + 2 ResMan Prospects for AN3/TRA)

#### Global Calls (1 time)
| Call | Endpoint | Type |
|------|----------|------|
| 1 | `Account/GetProperties` | Legacy |
| 2 | Google Sheets (Traffic) | HTTP GET |
| 3 | Google Sheets (Financial) | HTTP GET |

#### Per Property Calls (x12 properties) - OPTIMIZED

| Function | Endpoint | Type | Notes |
|----------|----------|------|-------|
| `fetch_leases()` | `GET /Leasing/Leases` | Partners | Shared by pending + expiring + future moves |
| `fetch_units_availability()` | `GET /Units/Availability` | Partners | Replaces Legacy GetUnitStatus |
| `get_work_orders_data()` | `GET /WorkOrders` | Partners | Work Orders feature |
| `get_receivables_mtd()` | `GET /Transactions/Receivables` | Partners | Receivable MTD / Del % (sums today's owed balances) |

**Per-property:** 4 Partners calls × 12 = 48.
**Prospects:** `GET /Prospects` for **AN3 & TRA only** = 2 (their traffic isn't in the CRM sheet).
**Current total:** 3 global + 48 + 2 = **~53 calls** (plus retries on failure).

### Optimizations Implemented (April 30, 2026)

#### 1. Merged Lease Calls ✓
- Created `fetch_leases()` helper function
- `get_pending_leases_data()` and `get_expiring_leases_data()` now accept pre-fetched leases
- **Savings:** 12 calls

#### 2. Merged Unit Calls ✓
- Created `fetch_partners_units()` helper function
- `get_units_renovation_data()` and `get_off_market_count()` now accept pre-fetched units
- **Savings:** 12 calls

#### 3. Reused GetUnitStatus ✓
- `get_property_data()` now returns `legacy_units`
- `get_off_market_count()` accepts `legacy_units` parameter
- **Savings:** 12 calls

#### 4. Added Work Orders Feature
- New `get_work_orders_data()` function
- **Added:** 12 calls

**Net Result (April 30):** the optimizations cut per-refresh calls ~16%. Later features (Work Orders, then Receivables + Prospects) added calls back — see the **Current total** above for the live figure.

---

## Recent Changes

### May 26, 2026 - ResMan Traffic for AN3/TRA, Daily AVG method, Receivables
- **Added:** `RESMAN_TRAFFIC_PROPERTIES = ["AN3", "TRA"]` with `fetch_prospects()` /
  `get_resman_traffic_for_property()` — AN3 & TRA traffic comes from Partners `GET /Prospects`
  (MTD by first-contact date, including Lost), since they aren't in the CRM Google Sheet.
- **Changed:** Traffic Daily AVG = MTD total ÷ days elapsed in the month (`now.day`) for ALL
  properties (was: distinct days with ≥1 lead). Zero-lead days now lower the average.
- **Added:** `get_receivables_mtd()` (Partners `GET /Transactions/Receivables?date=<today>`) →
  "Receivable MTD" ($) and "Del %" (Receivable ÷ Net) rows in the Financial Summary card, all 12 properties.

### May 19, 2026 - API Reliability Improvements
- **Added:** Retry logic with exponential backoff for Partners API
- **Added:** `make_partners_api_request()` helper function with built-in retry
- **Added:** Rate limiting protection (delays between API calls)
- **Migrated:** From Legacy `GetUnitStatus` to Partners `Units/Availability` endpoint
- **Optimized:** Property processing order (smallest properties first)
- **Fixed:** Graceful handling of API timeouts and 502 errors

### May 6, 2026 - Pending Transfer Support
- "Pending Transfer" leases now appear in Vacant Leased, NTV Leased, and Future MI popups
- Added yellow dot indicator with "Transfer" tooltip (similar to red alert dot)
- Transfers are NOT counted in Applicants metrics or popup
- Red alert dot takes priority if both conditions apply (past move-in date)

### UI Updates
- Added SNS logo to header (dark/light mode support)
- Logo size: w-14 h-14 (56px)

---

## Planned / Future Ideas

The Financial Summary card (income, credits, net, delinquency) and the **Receivable MTD / Del %**
metrics are now **shipped** — see Recent Changes. Remaining ideas:

- Individual property detail pages
- Historical trends and charts
- Automated daily email reports

> **Note on direct GL pulls:** per-account ledger endpoints (`/Transactions/Ledger`,
> `/Accounting/Balances`) remain impractical for bulk income — they require `billingAccountId`
> and would be thousands of calls per refresh. Income/credit totals therefore stay sourced from
> the n8n → Google Sheet pipeline; only the as-of-date **owed balances** are pulled live via
> `/Transactions/Receivables` (1 call per property).

---

## ResMan API Reference

### Legacy API
- **Base URL:** `https://api.myresman.com`
- **Auth:** POST with JSON body containing `IntegrationPartnerID`, `APIKey`, `AccountID`

| Endpoint | Purpose |
|----------|---------|
| `Account/GetProperties` | List all properties |
| `Property/GetUnitStatus` | Unit vacancy & ready status _(legacy — replaced by Partners `/Units/Availability`)_ |
| `Leasing/GetApplicantsAndCurrentResidents` | Resident status (evictions) |

### Partners API
- **Base URL:** `https://partners-api.myresman.com`
- **Auth:** Basic Auth (PartnerID:APIKey) + Header `ResMan-Account-Id`
- **Note:** This API can be slow/unreliable. Always use retry logic.

| Endpoint | Purpose | Notes |
|----------|---------|-------|
| `GET /Units/Availability` | Unit vacancy, occupancy, move-out dates | **Primary endpoint** |
| `GET /Leasing/Leases` | Lease data with history | Use `modifiedSince` param |
| `GET /Units` | Unit details, renovation, marketing status | |
| `GET /WorkOrders` | Work orders with status | Filter by `startDate` |
| `GET /Prospects` | MTD prospects by first contact | Used for AN3 & TRA traffic; filter `firstContactDateFrom` |
| `GET /Transactions/Receivables` | Owed account balances as of a date | Used for Receivable MTD / Del %; params `propertyId`, `date` |
| `GET /Transactions/Ledger` | Transactions | Requires billingAccountId |
| `GET /Accounting/Balances` | GL balances | Not working for us |

---

## Target Properties (12)

```python
TARGET_PROPERTIES = ["AN3", "TRA", "ROP", "TV", "MP", "SJ", "MUSO", "MOC", "EAST", "ORT", "49thStr", "AP"]
```

| Region | Properties |
|--------|------------|
| Tampa | 49thStr |
| Orlando | MP, TV, MOC, MUSO |
| Jacksonville | AP, SJ, ROP, ORT |
| Ft. Lauderdale | EAST, TRA, AN3 |

---

## File Structure

```
SNS Daily Report/
├── generate_dashboard.py   # Main script - fetches data, generates HTML
├── dashboard_live.html     # Generated output (deployed to Vercel)
├── renewal_letters.html    # Renewal letter generation tool
├── index.html              # Vercel entry point (redirects to dashboard)
├── README.md               # User documentation
├── DEVELOPMENT.md          # This file - technical notes
├── SNS_STYLEGUIDE.md       # Design system documentation
├── requirements.txt        # Python dependencies (requests, tzdata)
├── vercel.json             # Vercel configuration
├── .gitignore              # Git ignore rules
├── api/
│   ├── index.py            # POST /api/refresh - triggers GitHub Actions
│   ├── get_leases.py       # GET /api/get_leases - fetch leases for renewal
│   ├── get_charges.py      # GET /api/get_charges - fetch deposit/charges
│   └── push_monday.py      # POST /api/push_monday - sync to Monday.com
└── .github/
    └── workflows/
        └── refresh.yml     # GitHub Actions - auto-refresh every 4 hours
```

---

*Last Updated: May 26, 2026*
