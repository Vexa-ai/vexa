# The adoption panel's queries — one file per panel

**These files are the source of truth.** `../adoption-panel.json` is generated from them by
`../gen_dashboard.py`; editing the JSON instead of the SQL is caught by
`core/identity/services/admin-api/tests/test_stack_adoption_panel.py`.

Each file opens with a `--` comment block stating exactly what the panel counts, what it does
NOT count, and how it behaves when its input is missing. That block is lifted verbatim into the
panel's description in Grafana, so the definition travels with the number instead of living
only here — a reader hovering the ⓘ in a bank's Grafana gets the same words a reviewer reads in
this directory.

Two conventions every file follows:

- **`$window_days` and `$min_meetings` are Grafana textbox variables**, substituted as literal
  text before the statement reaches Postgres. Both are cast (`('$window_days')::int`) so a
  non-numeric value fails loudly at the database rather than composing into something that runs.
- **A missing `refs.participants` degrades, never errors.** Every roster read is wrapped in
  `CASE WHEN jsonb_typeof(...) = 'array' ... ELSE '[]'::jsonb END`, so an invite parsed without
  attendees, or a completion published by a domain that holds no invite, narrows the reading to
  organizer-only instead of failing the panel. `roster-coverage.sql` is how you see that it
  happened.

The full per-panel table, and the note that "active user" is still a founder-open definition,
are in [`../README.md`](../README.md).
