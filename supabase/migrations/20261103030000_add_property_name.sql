-- Add property_name to property_snapshots, backfill the 306 existing rows
-- from the authoritative data.json names, and refresh the two views so
-- queries surface the name without a client-side lookup.

alter table public.property_snapshots
    add column if not exists property_name text;

update public.property_snapshots
set property_name = case property_abbr
    when '49thStr' then '49th St Apartments'
    when 'AP'      then 'Arbor Place Apartments'
    when 'EAST'    then 'The Village at Eastpointe Apartments'
    when 'MOC'     then 'The Morgan At Ocala Apartments'
    when 'MUSO'    then 'The Mustang at Ocala'
    when 'ORT'     then 'The Villas at Ortega'
    when 'ROP'     then 'Riverside at Orange Park'
    when 'SJ'      then 'The Village at San Jose'
    when 'TV'      then 'The Vista At Winter Park'
end
where property_name is null;

-- Recreate portfolio_snapshots unchanged (name doesn't belong on a
-- cross-property view) -- no action needed here.

-- Recreate property_daily_deltas to include property_name for context.
create or replace view public.property_daily_deltas as
select
    s.id,
    s.snapshot_date,
    s.property_abbr,
    s.property_name,
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

alter view public.property_daily_deltas set (security_invoker = on);

grant select on public.property_daily_deltas to anon, authenticated;
