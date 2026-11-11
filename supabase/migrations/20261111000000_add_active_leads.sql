-- Add Active Leads columns to property_snapshots.
-- Source: Marketing Funnel sheet, "Active Leads" tab (snapshot of currently-active
-- leads per property). NULL = source dark / column didn't exist yet; 0 = no leads.

alter table property_snapshots
    add column if not exists leads_inquiry int,
    add column if not exists leads_tour_scheduled int,
    add column if not exists leads_tour_done int,
    add column if not exists active_leads_total int;

comment on column property_snapshots.leads_inquiry is 'Active leads in Inquiry status (Marketing Funnel → Active Leads tab).';
comment on column property_snapshots.leads_tour_scheduled is 'Active leads with Tour Scheduled.';
comment on column property_snapshots.leads_tour_done is 'Active leads with Tour Done.';
comment on column property_snapshots.active_leads_total is 'Sum of the three leads_* columns above. NULL if the Active Leads tab was unavailable.';
