-- SNS Daily Report — daily snapshot table.
-- One row per (property, snapshot_date). Written by supabase_sync.py (live)
-- and backfill_snapshots.py (historical).

create table if not exists public.property_snapshots (
    id              bigserial primary key,

    snapshot_date   date        not null,
    property_abbr   text        not null,
    region          text,
    generated_at    timestamptz,
    source          text        not null default 'live',  -- 'live' | 'backfill'
    created_at      timestamptz not null default now(),
    raw_jsonb       jsonb,

    -- Unit Status (point-in-time)
    total_units             int,
    occupied                int,
    vacant_ready            int,
    vacant_not_ready        int,
    ntv                     int,
    under_eviction          int,
    vacant_leased           int,
    ntv_leased              int,
    off_market              int,
    rentable_units          int,
    physical_occupancy_pct  numeric(5,2),
    occupancy_rented_pct    numeric(5,2),

    -- Traffic MTD (cumulative, resets 1st of each month)
    prospects_mtd            int,
    daily_avg_prospects      numeric(10,2),
    signed_leases_mtd        int,
    conversion_rate_pct      numeric(5,2),
    applicants_mtd           int,
    applicants_mtd_approved  int,
    applicants_mtd_denied    int,
    applicants_mtd_cancelled int,
    applicants_mtd_active    int,

    -- Traffic Snapshot (point-in-time)
    total_applicants  int,
    pending_approval  int,
    approved          int,
    signed_lease      int,

    -- Expiring Leases (M1..M4 forward buckets)
    exp_m1_label             text,
    exp_m1_current           int,
    exp_m1_pending_renewal   int,
    exp_m1_renewed           int,
    exp_m1_ntv_formers       int,
    exp_m1_under_eviction    int,
    exp_m1_renewal_pct       numeric(5,2),

    exp_m2_label             text,
    exp_m2_current           int,
    exp_m2_pending_renewal   int,
    exp_m2_renewed           int,
    exp_m2_ntv_formers       int,
    exp_m2_under_eviction    int,
    exp_m2_renewal_pct       numeric(5,2),

    exp_m3_label             text,
    exp_m3_current           int,
    exp_m3_pending_renewal   int,
    exp_m3_renewed           int,
    exp_m3_ntv_formers       int,
    exp_m3_under_eviction    int,
    exp_m3_renewal_pct       numeric(5,2),

    exp_m4_label             text,
    exp_m4_current           int,
    exp_m4_pending_renewal   int,
    exp_m4_renewed           int,
    exp_m4_ntv_formers       int,
    exp_m4_under_eviction    int,
    exp_m4_renewal_pct       numeric(5,2),

    -- Renovation
    renovated          int,
    partial_renovated  int,
    classic            int,

    -- Work Orders
    work_orders_open_total int,
    work_orders_new_24h    int,

    -- Future Moves (current month)
    future_mi_count int,
    future_mo_count int,

    -- Financial (MTD; resets monthly except receivable_mtd which is as-of)
    income_total           numeric(14,2),
    credit_total           numeric(14,2),
    income_net             numeric(14,2),
    receivable_mtd         numeric(14,2),
    delinquency_pct        numeric(5,2),
    delinq_current_amount  numeric(14,2),
    delinq_current_count   int,
    delinq_eviction_amount numeric(14,2),
    delinq_eviction_count  int,
    delinq_formers_amount  numeric(14,2),
    delinq_formers_count   int,

    constraint property_snapshots_unique_day
        unique (property_abbr, snapshot_date),
    constraint property_snapshots_source_valid
        check (source in ('live', 'backfill'))
);

create index if not exists property_snapshots_date_abbr_idx
    on public.property_snapshots (snapshot_date desc, property_abbr);

create index if not exists property_snapshots_abbr_date_idx
    on public.property_snapshots (property_abbr, snapshot_date desc);

comment on column public.property_snapshots.prospects_mtd is
    'Month-to-date count; resets on the 1st of each month.';
comment on column public.property_snapshots.signed_leases_mtd is
    'Month-to-date count; resets on the 1st of each month.';
comment on column public.property_snapshots.applicants_mtd is
    'Month-to-date count; resets on the 1st of each month.';
comment on column public.property_snapshots.income_total is
    'Month-to-date charges; resets on the 1st of each month.';
comment on column public.property_snapshots.credit_total is
    'Month-to-date credits; resets on the 1st of each month.';
comment on column public.property_snapshots.receivable_mtd is
    'Outstanding balance as of the snapshot moment (not a strict MTD).';


-- Portfolio-wide totals, one row per snapshot_date.
create or replace view public.portfolio_snapshots as
select
    snapshot_date,
    count(*) filter (where source = 'live')     as properties_live,
    count(*) filter (where source = 'backfill') as properties_backfill,

    sum(total_units)        as total_units,
    sum(occupied)           as occupied,
    sum(vacant_ready)       as vacant_ready,
    sum(vacant_not_ready)   as vacant_not_ready,
    sum(ntv)                as ntv,
    sum(under_eviction)     as under_eviction,
    sum(vacant_leased)      as vacant_leased,
    sum(ntv_leased)         as ntv_leased,
    sum(off_market)         as off_market,
    sum(rentable_units)     as rentable_units,
    round(
        case when sum(total_units) > 0
             then (sum(total_units) - sum(vacant_ready) - sum(vacant_not_ready))::numeric
                  / sum(total_units) * 100
             else 0 end,
        2
    ) as physical_occupancy_pct,
    round(
        case when sum(total_units) > 0
             then (sum(total_units) - sum(vacant_ready) - sum(vacant_not_ready)
                   + sum(vacant_leased))::numeric
                  / sum(total_units) * 100
             else 0 end,
        2
    ) as occupancy_rented_pct,

    sum(prospects_mtd)            as prospects_mtd,
    sum(signed_leases_mtd)        as signed_leases_mtd,
    sum(applicants_mtd)           as applicants_mtd,
    sum(applicants_mtd_approved)  as applicants_mtd_approved,
    sum(applicants_mtd_denied)    as applicants_mtd_denied,
    sum(applicants_mtd_cancelled) as applicants_mtd_cancelled,
    sum(applicants_mtd_active)    as applicants_mtd_active,

    sum(total_applicants) as total_applicants,
    sum(pending_approval) as pending_approval,
    sum(approved)         as approved,
    sum(signed_lease)     as signed_lease,

    sum(renovated)         as renovated,
    sum(partial_renovated) as partial_renovated,
    sum(classic)           as classic,

    sum(work_orders_open_total) as work_orders_open_total,
    sum(work_orders_new_24h)    as work_orders_new_24h,

    sum(future_mi_count) as future_mi_count,
    sum(future_mo_count) as future_mo_count,

    sum(income_total)           as income_total,
    sum(credit_total)           as credit_total,
    sum(income_net)             as income_net,
    sum(receivable_mtd)         as receivable_mtd,
    sum(delinq_current_amount)  as delinq_current_amount,
    sum(delinq_current_count)   as delinq_current_count,
    sum(delinq_eviction_amount) as delinq_eviction_amount,
    sum(delinq_eviction_count)  as delinq_eviction_count,
    sum(delinq_formers_amount)  as delinq_formers_amount,
    sum(delinq_formers_count)   as delinq_formers_count,
    round(
        case when (sum(income_total) - sum(credit_total)) > 0
             then sum(receivable_mtd)
                  / (sum(income_total) - sum(credit_total)) * 100
             else 0 end,
        2
    ) as delinquency_pct
from public.property_snapshots
group by snapshot_date;


-- Day-over-day deltas for cumulative MTD fields.
-- LAG() partitioned by (property, month) resets automatically on the 1st.
create or replace view public.property_daily_deltas as
select
    s.id,
    s.snapshot_date,
    s.property_abbr,
    s.region,

    s.prospects_mtd,
    s.prospects_mtd - lag(s.prospects_mtd) over w       as delta_prospects,

    s.signed_leases_mtd,
    s.signed_leases_mtd - lag(s.signed_leases_mtd) over w   as delta_signed_leases,

    s.applicants_mtd,
    s.applicants_mtd - lag(s.applicants_mtd) over w         as delta_applicants,

    s.applicants_mtd_approved,
    s.applicants_mtd_approved - lag(s.applicants_mtd_approved) over w   as delta_applicants_approved,

    s.applicants_mtd_denied,
    s.applicants_mtd_denied - lag(s.applicants_mtd_denied) over w       as delta_applicants_denied,

    s.applicants_mtd_cancelled,
    s.applicants_mtd_cancelled - lag(s.applicants_mtd_cancelled) over w as delta_applicants_cancelled,

    s.income_total,
    s.income_total - lag(s.income_total) over w             as delta_income,

    s.credit_total,
    s.credit_total - lag(s.credit_total) over w             as delta_credit
from public.property_snapshots s
window w as (
    partition by s.property_abbr, date_trunc('month', s.snapshot_date)
    order by s.snapshot_date
);
