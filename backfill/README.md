# Backfill folder

Drop historical `dashboard-YYYY-MM-DD.xlsx` files here, then:

```
# Dry-run (prints a sample parsed row)
python backfill_snapshots.py --dir backfill --dry-run

# Apply (requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in env)
python backfill_snapshots.py --dir backfill --apply
```

Rows written from here are tagged `source='backfill'`. Live daily rows are
tagged `source='live'`. Both coexist in `property_snapshots` and only need
to be differentiated in queries if you care about provenance.

Filename convention: `dashboard-YYYY-MM-DD.xlsx` (the date is parsed from
the filename). If the filename doesn't contain a date the parser falls
back to reading the title cell.
