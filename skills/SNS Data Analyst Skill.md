---
name: sns-dashboard-read
description: Read-only access to the SNS property portfolio's daily Supabase snapshots. Use when asked to analyse SNS occupancy, applicants, renewals, leasing pipeline, work orders, delinquency, or any trend derived from the SNS Daily Report.
---

# SNS Daily Report — Supabase Read-Only Reference

You have read-only access to the SNS property portfolio's daily snapshot database.
One row per property per calendar date (America/New_York). History runs from
**2026-10-01** onward; the pipeline writes a fresh row every day at **midnight ET**.

Use this reference to answer questions about:

- Occupancy, vacancy, and NTV trends (daily, weekly, monthly)
- Leasing pipeline: prospects, applicants, signed leases, renewals
- Financial state: income, credits, receivables, delinquency
- Expiring-lease forecasts (4 months ahead)
- Renovation mix, work-order load
- Any day-over-day change for the above

---

## Connection

**Base URL:** `https://judbcwsdalgfwaxzhxlq.supabase.co/rest/v1/`

**Publishable (read-only) key:** `sb_publishable_1azsM_VqxKPLLs5FB_AwTg_NKx-sLnQ`

**Required headers on every request:**
```
apikey: sb_publishable_1azsM_VqxKPLLs5FB_AwTg_NKx-sLnQ
Authorization: Bearer sb_publishable_1azsM_VqxKPLLs5FB_AwTg_NKx-sLnQ
```

The key is read-only. Attempting `POST`/`PATCH`/`DELETE` returns `401 Unauthorized`.

If you are running in an environment that treats Supabase secret keys as
browser-originated, the API will reject them. The publishable key is fine from
anywhere.

---

## Properties in scope

| Abbr | Name | Region | Total units |
|---|---|---|---:|
| `49thStr` | 49th St Apartments | Tampa | 286 |
| `TV` | The Vista At Winter Park | Orlando | 108 |
| `MOC` | The Morgan At Ocala Apartments | Orlando | 241 |
| `MUSO` | The Mustang at Ocala | Orlando | 202 |
| `AP` | Arbor Place Apartments | Jacksonville | 206 |
| `SJ` | The Village at San Jose | Jacksonville | 199 |
| `ROP` | Riverside at Orange Park | Jacksonville | 105 |
| `ORT` | The Villas at Ortega | Jacksonville | 444 |
| `EAST` | The Village at Eastpointe Apartments | Ft. Lauderdale | 296 |

Unit counts vary +/- a few over time. Always query `total_units` from
`property_snapshots` for the authoritative value on a given date.

The `region` column groups MOC and MUSO under "Orlando" even though their
names reference Ocala -- that's the portfolio's internal regional grouping.
Never rename or recategorize a property; use the Abbr + Name exactly as above.

---

## Tables and views

### `property_snapshots` (table)
One row per `(property_abbr, snapshot_date)`. The primary data source.

### `portfolio_snapshots` (view)
Portfolio totals, one row per `snapshot_date`. Sums across all 9 properties.
Use this for "all properties combined" questions. Percentages are re-computed
from summed counts (not averaged across properties).

### `property_daily_deltas` (view)
Day-over-day deltas for the cumulative MTD columns. Uses
`LAG() PARTITION BY (property_abbr, month(snapshot_date))`, so **on the 1st of a
month the delta restarts from the raw value** (because the window partition
resets). This correctly distinguishes "zero activity today" from "fresh month".

---

## Field reference — `property_snapshots`

### Identity & provenance
| Column | Type | Meaning |
|---|---|---|
| `id` | bigserial | surrogate primary key |
| `snapshot_date` | date | calendar date in ET for which this row represents |
| `property_abbr` | text | one of the 9 abbreviations above |
| `region` | text | Tampa / Orlando / Jacksonville / Ft. Lauderdale |
| `generated_at` | timestamptz | moment the source `data.json` was generated |
| `source` | text | `live` (via n8n/Vercel) or `backfill` (historical xlsx import) |
| `created_at` | timestamptz | insertion time into Supabase |
| `raw_jsonb` | jsonb | forensic blob: the per-property slice of `data.json` as captured |

### Unit Status (point-in-time snapshot)
| Column | Type | Meaning |
|---|---|---|
| `total_units` | int | all marketable units on the property (excludes Down/Abated) |
| `occupied` | int | units with an active lease and resident on site |
| `vacant_ready` | int | vacant, make-ready complete, listable |
| `vacant_not_ready` | int | vacant, make-ready in progress |
| `ntv` | int | occupied but resident has given Notice To Vacate |
| `under_eviction` | int | units with an active eviction case |
| `vacant_leased` | int | vacant unit with a signed future lease (move-in upcoming) |
| `ntv_leased` | int | NTV unit with an already-signed replacement lease |
| `off_market` | int | available but not listed for online marketing |
| `rentable_units` | int | derived: vacant_ready + vacant_not_ready + ntv − vacant_leased − ntv_leased |
| `physical_occupancy_pct` | numeric(5,2) | (total − vacant_ready − vacant_not_ready) / total × 100 |
| `occupancy_rented_pct` | numeric(5,2) | includes pre-leased vacant units in the "occupied" bucket |

### Traffic MTD — **cumulative, resets the 1st of each month**
| Column | Type | Meaning |
|---|---|---|
| `prospects_mtd` | int | total new lead inquiries this month |
| `daily_avg_prospects` | numeric(10,2) | `prospects_mtd` / days-elapsed-in-month |
| `signed_leases_mtd` | int | new leases signed this month (excludes renewals) |
| `conversion_rate_pct` | numeric(5,2) | `signed_leases_mtd` / `prospects_mtd` × 100 |
| `applicants_mtd` | int | total applications submitted this month |
| `applicants_mtd_approved` | int | approved MTD |
| `applicants_mtd_denied` | int | denied MTD |
| `applicants_mtd_cancelled` | int | cancelled MTD |
| `applicants_mtd_active` | int | still-active applicants from MTD |

⚠️ **Data gap:** rows with `snapshot_date` between `2026-10-01` and `2026-10-27`
have NULL for `applicants_mtd`, `applicants_mtd_approved`, `applicants_mtd_denied`,
`applicants_mtd_cancelled`, `applicants_mtd_active`, and `conversion_rate_pct` —
the pre-2026-10-28 excel layout didn't carry these fields. For those days,
treat the columns as unknown, not zero.

### Traffic Snapshot (point-in-time)
| Column | Type | Meaning |
|---|---|---|
| `total_applicants` | int | currently in the funnel (pending + approved + signed) |
| `pending_approval` | int | applied, awaiting decision |
| `approved` | int | approved, not yet signed |
| `signed_lease` | int | signed, awaiting move-in |

### Expiring Leases — 4 forward buckets (M1 is the snapshot_date's month)
For each of `m1`, `m2`, `m3`, `m4`:

| Column | Type | Meaning |
|---|---|---|
| `exp_mN_label` | text | human month label, e.g. `"Oct 2026"`, `"Jan 2027"` |
| `exp_mN_current` | int | lease ends in that month, resident has NOT decided |
| `exp_mN_pending_renewal` | int | offer sent, not countersigned yet |
| `exp_mN_renewed` | int | renewed |
| `exp_mN_ntv_formers` | int | NTV + already-moved-out in that month |
| `exp_mN_under_eviction` | int | lease expiring but under eviction |
| `exp_mN_renewal_pct` | numeric(5,2) | `renewed / (renewed + current + ntv_formers + under_eviction) × 100` |

### Renovation (point-in-time snapshot)
| Column | Type | Meaning |
|---|---|---|
| `renovated` | int | fully renovated units |
| `partial_renovated` | int | partially renovated |
| `classic` | int | original / unrenovated |

### Work Orders (point-in-time snapshot)
| Column | Type | Meaning |
|---|---|---|
| `work_orders_open_total` | int | all open work orders excluding make-readies |
| `work_orders_new_24h` | int | work orders opened in the last 24 hours before snapshot |

### Future Moves (current month)
| Column | Type | Meaning |
|---|---|---|
| `future_mi_count` | int | move-ins scheduled in the snapshot_date's month |
| `future_mo_count` | int | move-outs scheduled in the snapshot_date's month |

⚠️ `future_mi_count` is only populated for `source='live'` rows. Backfilled
days have it NULL (the historical xlsx didn't export it).

### Financial (MTD unless noted)
| Column | Type | Meaning |
|---|---|---|
| `income_total` | numeric(14,2) | gross charges MTD; resets 1st of month |
| `credit_total` | numeric(14,2) | credits applied MTD; resets 1st of month |
| `income_net` | numeric(14,2) | `income_total − credit_total` |
| `receivable_mtd` | numeric(14,2) | outstanding balance **as of the snapshot moment** (NOT strict MTD) |
| `delinquency_pct` | numeric(5,2) | `receivable_mtd / income_net × 100` |
| `delinq_current_amount` | numeric(14,2) | $ owed by current residents |
| `delinq_current_count` | int | # of current-resident accounts in arrears |
| `delinq_eviction_amount` | numeric(14,2) | $ owed, under eviction |
| `delinq_eviction_count` | int | # of eviction cases with debt |
| `delinq_formers_amount` | numeric(14,2) | $ owed by moved-out residents |
| `delinq_formers_count` | int | # of former-resident accounts in collections |

---

## Field reference — `portfolio_snapshots` (view)

Same column set as `property_snapshots`, **summed across all properties per
`snapshot_date`**, with the following differences:

- No `property_abbr` / `region` / `raw_jsonb` / `id`.
- `physical_occupancy_pct` and `occupancy_rented_pct` are **recomputed** from
  the summed counts (correct), not averaged over properties (would be wrong).
- `delinquency_pct` is likewise recomputed.
- Adds `properties_live` and `properties_backfill` (count of rows included
  for the date — normally 9 of one source or a mix during the backfill era).

---

## Field reference — `property_daily_deltas` (view)

For each `(property_abbr, snapshot_date)` returns:

| Column | Meaning |
|---|---|
| `prospects_mtd` + `delta_prospects` | today's cumulative + day-over-day change |
| `signed_leases_mtd` + `delta_signed_leases` | same |
| `applicants_mtd` + `delta_applicants` | same |
| `applicants_mtd_approved` + `delta_applicants_approved` | same |
| `applicants_mtd_denied` + `delta_applicants_denied` | same |
| `applicants_mtd_cancelled` + `delta_applicants_cancelled` | same |
| `income_total` + `delta_income` | same |
| `credit_total` + `delta_credit` | same |

⚠️ `delta_*` is **NULL for the first row of each month** (nothing to diff against
within the month's window), and **restarts from the raw value on the 1st of a
month** — because the month boundary is the window partition. Treat NULL deltas
as "unknown / month just started", not zero.

---

## PostgREST filter syntax — one-page cheatsheet

PostgREST exposes Postgres over HTTP. Every URL is a `SELECT`.

```
?select=col1,col2         -> project columns
?order=snapshot_date.desc -> ORDER BY ... DESC (asc is default)
?limit=50                 -> LIMIT
?offset=100               -> OFFSET
```

### Filters (`col=operator.value`)
| Operator | Meaning | Example |
|---|---|---|
| `eq` | equals | `property_abbr=eq.AP` |
| `neq` | not equal | `property_abbr=neq.ORT` |
| `gt` / `gte` | greater (or equal) | `snapshot_date=gte.2026-10-15` |
| `lt` / `lte` | less (or equal) | `physical_occupancy_pct=lt.90` |
| `in` | in list | `property_abbr=in.(AP,ORT,SJ)` |
| `is` | NULL check | `applicants_mtd=is.null` |
| `like` / `ilike` | pattern | `exp_m1_label=ilike.*2026*` |
| `and` / `or` | compound | `and=(property_abbr.eq.AP,snapshot_date.gte.2026-10-15)` |

### Aggregation
PostgREST does NOT execute arbitrary SQL. For aggregates (`AVG`, `MAX`, `SUM`),
either (a) pull the rows and aggregate client-side, or (b) query the views
which already contain pre-aggregated data.

---

## Example queries

### Latest row for one property
```
GET /rest/v1/property_snapshots?property_abbr=eq.AP&order=snapshot_date.desc&limit=1
```

### Occupancy trend for a property (last 30 days)
```
GET /rest/v1/property_snapshots
    ?property_abbr=eq.AP
    &select=snapshot_date,physical_occupancy_pct,occupancy_rented_pct
    &order=snapshot_date.asc
    &limit=30
```

### Portfolio-wide occupancy over time
```
GET /rest/v1/portfolio_snapshots
    ?select=snapshot_date,physical_occupancy_pct,total_units,occupied
    &order=snapshot_date.asc
```

### Properties with occupancy below 88% on the most recent date
Two-step: find the latest date, then filter.
```
GET /rest/v1/property_snapshots?select=snapshot_date&order=snapshot_date.desc&limit=1
# then:
GET /rest/v1/property_snapshots
    ?snapshot_date=eq.<latest_date>
    &physical_occupancy_pct=lt.88
    &select=property_abbr,physical_occupancy_pct,occupied,total_units
```

### Day-over-day prospect change for AP in October
```
GET /rest/v1/property_daily_deltas
    ?property_abbr=eq.AP
    &snapshot_date=gte.2026-10-01
    &snapshot_date=lte.2026-10-31
    &select=snapshot_date,prospects_mtd,delta_prospects
    &order=snapshot_date.asc
```

### Expiring leases 3 months out across the portfolio
```
GET /rest/v1/property_snapshots
    ?snapshot_date=eq.<latest_date>
    &select=property_abbr,exp_m3_label,exp_m3_current,exp_m3_pending_renewal,exp_m3_renewed,exp_m3_renewal_pct
    &order=exp_m3_renewal_pct.asc
```

### Delinquency snapshot
```
GET /rest/v1/property_snapshots
    ?snapshot_date=eq.<latest_date>
    &select=property_abbr,delinquency_pct,delinq_current_amount,delinq_current_count,delinq_eviction_amount,delinq_formers_amount
    &order=delinquency_pct.desc
```

### Full raw row for a specific date + property (debugging)
```
GET /rest/v1/property_snapshots
    ?property_abbr=eq.AP
    &snapshot_date=eq.2026-11-01
    &select=*
```

---

## Example code (any language)

### Python
```python
import requests

URL = "https://judbcwsdalgfwaxzhxlq.supabase.co/rest/v1"
KEY = "<publishable_key>"
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

def latest_for_property(abbr):
    r = requests.get(
        f"{URL}/property_snapshots",
        params={"property_abbr": f"eq.{abbr}", "order": "snapshot_date.desc", "limit": 1},
        headers=H, timeout=30,
    )
    r.raise_for_status()
    return r.json()[0] if r.json() else None

print(latest_for_property("AP"))
```

### curl
```
curl -s \
  "https://judbcwsdalgfwaxzhxlq.supabase.co/rest/v1/portfolio_snapshots?select=snapshot_date,physical_occupancy_pct&order=snapshot_date.asc" \
  -H "apikey: <publishable_key>" \
  -H "Authorization: Bearer <publishable_key>"
```

### JavaScript (`fetch`)
```js
const URL = "https://judbcwsdalgfwaxzhxlq.supabase.co/rest/v1";
const KEY = "<publishable_key>";
const H = { apikey: KEY, Authorization: `Bearer ${KEY}` };

const resp = await fetch(
  `${URL}/property_snapshots?property_abbr=eq.AP&order=snapshot_date.desc&limit=30&select=snapshot_date,physical_occupancy_pct`,
  { headers: H }
);
const rows = await resp.json();
```

---

## When answering a user, prefer these patterns

- "How is the portfolio doing?" → `portfolio_snapshots`, latest date.
- "Compare properties on X" → `property_snapshots`, filter to one date.
- "Trend X over time" → `property_snapshots` or `portfolio_snapshots`, ordered.
- "What changed yesterday for X?" → `property_daily_deltas` for the `delta_*` columns.
- "Monthly MTD question" → always query the latest `snapshot_date` of that month;
  don't try to sum daily rows — these are cumulative counters.

Always surface the **date** of the data you're quoting ("as of 2026-11-03")
so the user knows how fresh the answer is.

---

## Known limitations

- Granularity is **one row per property per day**. There is no sub-daily
  history. If a question requires hourly or intra-day data, say so.
- No unit-level history — this DB holds aggregated property-level metrics only.
- No applicant-level history — only counts.
- The database only has data from **2026-10-01 onward**.
- Pre-2026-10-28 rows have NULL `applicants_mtd_*` and `conversion_rate_pct`
  (see the gap note above).
- Backfilled rows (`source='backfill'`) don't have `future_mi_count` populated.
