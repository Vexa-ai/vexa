# MIGRATION-0012 — remove stored Zoom host tokens (`zak`) and mask passcodes in stored diagnostics

**Status:** an **out-of-band, human-run ops step** (like MIGRATION-0001..0003). Data only: no
column, index or table changes, so `schema.seal.json` is unchanged. `ensure_schema` does NOT run it.

## What it fixes

- **Host tokens.** A host's own calendar carries Zoom START links, `https://zoom.us/s/<id>?zak=<token>`
  or a join link with `&zak=<token>`. The token starts the meeting as its host. Calendar sync stored
  the link as the meeting's `constructed_meeting_url`, kept the raw iCalendar snapshot under
  `calendar_sources[].event`, and a bot that failed on the link quoted it in its diagnostics.
- **Passcodes in diagnostics.** A bot that failed on a link quoted it, attendee passcode included
  (Zoom `pwd`, Teams `p`, Jitsi `jwt`…), in `reason`, `last_error`, `join_evidence` and
  `status_transition` — free text that people read and webhooks carry, never a link anything joins.

From this release the token is dropped on the way in (`meeting_url_redaction.strip_host_tokens`, at
`POST /bots`, the planned-meeting routes and calendar sync), and a bot redacts its error text before
it reports it. Rows written before that still hold both. This step removes them.

## The change

`python -m meeting_api.scrub_host_tokens` (`meeting_api/scrub_host_tokens.py`), run inside a
meeting-api pod of this release (it reads the same database settings the service does):

- selects `meetings` rows where an http(s) URL in `data` carries `zak=`, or an http(s) URL in one of
  the four diagnostic keys carries an unmasked `pwd`/`password`/`passcode`/`p`/`tk`/`token`/`jwt`
  (after `?`, `&`, `#` or `&amp;`). Only URLs are selected because only URLs are rewritten;
- walks each row's `data`: in every string, `zak` is removed from every URL; under `reason`,
  `last_error`, `join_evidence` and `status_transition` the secret parameter values are also masked
  to `***` (`redact_urls_in`: host, path and meeting id stay). `constructed_meeting_url` and
  `calendar_sources` keep the attendee passcode, because auto-join joins from them;
- writes each changed string with `jsonb_set` at its one path: every other value keeps its stored
  bytes; no other column (not `updated_at`) is written;
- batches of 200 rows, each batch one transaction, rows locked `FOR UPDATE`;
- **dry run by default**: prints the counts of each kind and the top-level `data` keys they sit
  under, writes nothing. `--apply` rewrites, then re-counts and exits 1 if any row still matches.
- Idempotent: a masked value reads `***`, which the selection does not match, and a second `--apply`
  finds no rows.

Output is counts and key names only, never a token, a passcode or a URL. Proven on Postgres by
`core/meetings/services/meeting-api/tests/test_scrub_host_tokens_postgres.py`.

## Production rollout

After the meeting-api of this release is live in `vexa-production`:

### 1. Pre-flight count (read-only)

The same selection, as SQL, through the read-only path:

```bash
~/dev/vexa-secrets/bin/prod-db-read scrub-dryrun <<'SQL'
WITH p AS (SELECT 'https?://[^[:space:]"''<>`\\]*([?&#]|&amp;)zak=' AS host,
                  'https?://[^[:space:]"''<>`\\]*([?&#]|&amp;)(pwd|password|passcode|p|tk|token|jwt)=[^*&#[:space:]"\\]' AS pass)
SELECT count(*) FILTER (WHERE data::text ~* p.host) AS host_token_rows,
       count(*) FILTER (WHERE jsonb_build_array(data->'reason', data->'last_error', data->'join_evidence', data->'status_transition')::text ~* p.pass) AS passcode_rows,
       count(*) FILTER (WHERE data::text ~* p.host OR jsonb_build_array(data->'reason', data->'last_error', data->'join_evidence', data->'status_transition')::text ~* p.pass) AS rows_total
FROM meetings, p;
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
every count must be `0`.

## Not covered

Copies outside this database: webhook payloads already delivered to customer endpoints, pod logs
already shipped, and backups. Those age out on their own retention. The join links keep their
attendee passcode by design.
