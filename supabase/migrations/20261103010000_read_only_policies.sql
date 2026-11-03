-- Make property_snapshots (and its views) safely readable via the publishable
-- (anon) key while still writable only via the secret/service-role key.
--
-- After this runs:
--   - The publishable key can SELECT rows + views.
--   - The publishable key CANNOT insert/update/delete (no write policy).
--   - The secret key keeps full access (service_role bypasses RLS).

alter table public.property_snapshots enable row level security;

drop policy if exists "Anyone can read property_snapshots"
    on public.property_snapshots;

create policy "Anyone can read property_snapshots"
    on public.property_snapshots
    for select
    using (true);

-- Views inherit the base table's RLS automatically. Grants may need a nudge:
grant select on public.property_snapshots  to anon, authenticated;
grant select on public.portfolio_snapshots to anon, authenticated;
grant select on public.property_daily_deltas to anon, authenticated;
