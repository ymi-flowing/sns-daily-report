# SNS Daily Report - Development Notes

## API Call Analysis

### Current API Calls Per Dashboard Refresh (After Optimization)

**Total: 62 API calls** (reduced from 74, with Work Orders feature added)

#### Global Calls (1 time)
| Call | Endpoint | Type |
|------|----------|------|
| 1 | `Account/GetProperties` | Legacy |
| 2 | Google Sheets (Traffic) | HTTP GET |

#### Per Property Calls (x12 properties) - OPTIMIZED

| Function | Endpoint | Type | Notes |
|----------|----------|------|-------|
| `get_property_data()` | `Property/GetUnitStatus` | Legacy | Returns legacy_units for reuse |
| `get_property_data()` | `Leasing/GetApplicantsAndCurrentResidents` | Legacy | |
| `fetch_leases()` | `GET /Leasing/Leases` | Partners | **MERGED** - shared by pending + expiring |
| `fetch_partners_units()` | `GET /Units` | Partners | **MERGED** - shared by renovation + off-market |
| `get_work_orders_data()` | `GET /WorkOrders` | Partners | **NEW** - Work Orders feature |

**After Optimization:** 2 + (12 x 5) = **62 calls**

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

**Net Result:** 74 - 36 + 12 = **62 calls** (16% reduction + new feature)

---

## Planned Features

### Financial Summary Card (ON HOLD)

**Status:** Waiting for data pipeline

**Plan:**
1. ResMan scheduled report exports financial data
2. n8n workflow processes and sends to Google Sheet
3. Dashboard reads from Google Sheet (1 API call vs 6,000+)

**Data Required:**
| Metric | Description |
|--------|-------------|
| Total Charges | Sum of all charges for the month |
| Total Credits | Sum of all credits for the month |
| Total Income | Charges - Credits |

**Why Not Direct API:**
- ResMan `/Transactions/Ledger` requires `billingAccountId` parameter
- No bulk property-level endpoint exists
- Would require ~500 calls per property (one per billing account)
- 12 properties x 500 accounts = 6,000+ API calls
- Tested alternatives: `/Accounting/Balances`, `GetBudgetAndActual` - return empty

**Google Sheet Format (TBD):**
```
Property | Charges | Credits | Income | Month
EAST     | 596939  | 18410   | 578529 | 2026-04
...
```

---

## ResMan API Reference

### Legacy API
- **Base URL:** `https://api.myresman.com`
- **Auth:** POST with JSON body containing `IntegrationPartnerID`, `APIKey`, `AccountID`

| Endpoint | Purpose |
|----------|---------|
| `Account/GetProperties` | List all properties |
| `Property/GetUnitStatus` | Unit vacancy & ready status |
| `Leasing/GetApplicantsAndCurrentResidents` | Resident status (evictions) |

### Partners API
- **Base URL:** `https://partners-api.myresman.com`
- **Auth:** Basic Auth (PartnerID:APIKey) + Header `ResMan-Account-Id`

| Endpoint | Purpose |
|----------|---------|
| `GET /Leasing/Leases` | Lease data with history |
| `GET /Units` | Unit details, renovation, marketing status |
| `GET /Transactions/Ledger` | Transactions (requires billingAccountId) |
| `GET /Accounting/Balances` | GL balances (not working for us) |
| `GET /Accounting/ChartOfAccounts` | Chart of accounts |

---

## Target Properties (12)

```python
TARGET_PROPERTIES = ["MUSO", "ORT", "49thStr", "ROP", "AP", "SJ", "EAST", "MOC", "TV", "MP", "TRA", "AN3"]
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
├── dashboard_live.html     # Generated output (open in browser)
├── README.md               # User documentation
├── DEVELOPMENT.md          # This file - technical notes
├── SNS_STYLEGUIDE.md       # Design system documentation
├── requirements.txt        # Python dependencies
├── Cardentials.txt         # API credentials (in .gitignore)
├── index.html              # Vercel entry point
├── vercel.json             # Vercel configuration
├── api/
│   └── index.py            # Vercel serverless function (refresh trigger)
├── .github/
│   └── workflows/
│       └── refresh.yml     # GitHub Actions auto-refresh
└── archive/                # Old exploration scripts
```

---

*Last Updated: April 30, 2026*
