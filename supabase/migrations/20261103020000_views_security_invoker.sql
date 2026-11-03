-- Make the views honor the caller's RLS instead of running as the view
-- owner (postgres, which bypasses RLS). After this, portfolio_snapshots
-- and property_daily_deltas are gated by the SELECT policy on the base
-- table and the Supabase dashboard stops flagging them as UNRESTRICTED.

alter view public.portfolio_snapshots    set (security_invoker = on);
alter view public.property_daily_deltas  set (security_invoker = on);
