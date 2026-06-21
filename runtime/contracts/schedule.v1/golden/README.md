# golden vectors — schedule.v1

The spec by example (P8). Each `<Shape>.<case>.json` validates against `#/$defs/<Shape>`:

- `ScheduleJob.one-shot.json` — a one-time dispatch at a fixed `execute_at`, with retry + idempotency_key.
- `ScheduleJob.cron.json` — a recurring job (`cron`) that re-arms after each successful run.

Secrets are placeholders (P14). `node ../validate.mjs` checks all goldens against the schema.
