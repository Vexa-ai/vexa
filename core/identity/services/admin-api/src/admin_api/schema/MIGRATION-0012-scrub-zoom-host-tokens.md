# MIGRATION-0012 — remove stored Zoom host tokens (`zak`) from `meetings.data`

**Status:** an **out-of-band, human-run ops step** (like MIGRATION-0001..0003). Data only: no
column, index or table changes, so `schema.seal.json` is unchanged. `ensure_schema` does NOT run it.

## What it fixes

A host's own calendar carries Zoom START links, `https://zoom.us/s/<id>?zak=<token>` or a join link
with `&zak=<token>`. The token starts the meeting as its host. Calendar sync stored the link it found
as the meeting's `constructed_meeting_url`, kept the raw iCalendar snapshot under
`calendar_sources[].event`, and a bot that failed on that link quoted it in `reason`, `last_error`,
`status_transition[].reason` and `join_evidence.detail`.

From this release the token is dropped on the way in (`meeting_url_redaction.strip_host_tokens`, at
`POST /bots`, the planned-meeting routes and calendar sync), and a bot's error text is redacted
before it is reported. Rows written before that still hold the token. This step removes it.

## The change

`python -m meeting_api.scrub_host_tokens` (`meeting_api/scrub_host_tokens.py`), run inside a
meeting-api pod of this release (it reads the same database settings the service does):

- selects `meetings` rows whose `data::text` matches `[?&#;]zak=`;
- walks each row's `data` and, for every string whose URLs carry a `zak` parameter, writes the same
  string without that parameter with `jsonb_set` at that one path. The attendee passcode (`pwd`)
  and every other byte of the string stay; every other value keeps its stored bytes; no other column
  (not `updated_at`) is written;
- batches of 200 rows, each batch one transaction, rows locked `FOR UPDATE`;
- **dry run by default**: prints the count of affected rows and the top-level `data` keys they sit
  under, writes nothing. `--apply` rewrites, then re-counts and exits 1 if any row still matches.
- Idempotent: a second `--apply` finds no rows.

Output is counts and key names only, never a token or a URL. Proven on Postgres by
`core/meetings/services/meeting-api/tests/test_scrub_host_tokens_postgres.py`.

## Production rollout

After the meeting-api of this release is live in `vexa-production`:

### 1. Pre-flight count (read-only)

```bash
~/dev/vexa-secrets/bin/prod-db-read zak-count <<'SQL'
SELECT e.key, count(*) AS rows, count(DISTINCT m.user_id) AS users
FROM meetings m, jsonb_each(m.data) e
WHERE m.data::text ~* '[?&#;]zak=' AND e.value::text ~* '[?&#;]zak='
GROUP BY e.key ORDER BY rows DESC;
SELECT count(*) AS rows FROM meetings WHERE data::text ~* '[?&#;]zak=';
SQL
```

### 2. Dry run, then apply

```bash
POD=$(kubectl -n vexa-production get pod -l app.kubernetes.io/component=meeting-api \
      -o jsonpath='{.items[0].metadata.name}')
kubectl -n vexa-production exec "$POD" -- python -m meeting_api.scrub_host_tokens          # counts only
kubectl -n vexa-production exec "$POD" -- python -m meeting_api.scrub_host_tokens --apply  # rewrites
```

`--apply` prints `{"after": {"rows": 0, ...}, "changed_rows": N, ...}` and exits 0. Re-run step 1:
both queries must return no rows / `0`.

## Not covered

Copies outside this database: webhook payloads already delivered to customer endpoints, pod logs
already shipped, and backups. Those age out on their own retention.
