# MIGRATION-0004 — backfill empty API-token scopes (0.10 → 0.12 grandfather)

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_backfill_token_scopes`),
after the additive column sync and before the index sync. Unlike MIGRATION-0001..0003 (out-of-band,
human-run ops steps), this one **runs on every admin-api boot** because the failure it prevents is a
silent, deploy-time-invisible revocation of every pre-existing customer token — it must converge at
cutover with no operator action. Idempotent: it only touches empty/NULL rows, so it no-ops once there
are none. Closes #578.

## The bug it fixes

0.10's `api_tokens` table had **no `scopes` column** — keys were effectively unscoped (any valid key
reached every route). 0.12 adds:

```py
scopes = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))   # models.py
```

On upgrade over a **populated** `api_tokens` table, `_sync_columns`'s additive `ALTER TABLE ADD COLUMN`
fills every existing row with `'{}'` (empty array). From then on:

- `app/main.py::get_current_user` → `token_scopes = set(row.scopes) if row.scopes else set()` → empty →
  403 when `not token_scopes & VALID_SCOPES`.
- `app/main.py` `/internal/validate` → `["legacy"]`, which is not in `VALID_SCOPES {bot,tx,browser}`.
- `gateway/app.py` `ROUTE_SCOPES` enforcement → 403 on `/bots`, `/transcripts`, `/meetings`, `/recordings`.

Net: every pre-existing token silently 403s on all core routes after the upgrade. New installs and
newly-minted tokens are unaffected (mint always sets scopes), so the bug is invisible unless you upgrade
a populated table.

## The change

```sql
UPDATE api_tokens SET scopes = '{bot,tx,browser}'
WHERE scopes = '{}'::text[] OR scopes IS NULL;
```

Full valid-scope set = mirrors 0.10's unscoped allow-all behavior, and makes the DB self-describing (the
`/internal/validate` path stays simple — no read-time remapping of empty/`legacy`). The `WHERE` predicate
touches only empties, so:

- already-scoped rows (a token deliberately minted `{tx}`) are **never widened**;
- re-running `ensure_schema` is a no-op once no empty rows remain (idempotent).

`scopes` is `NOT NULL`, so `IS NULL` never matches in practice — it is kept only as a defensive predicate.

### Scope-prefix refinement — deliberately NOT taken

The issue floats an optional refinement: where the token value carries a `vxa_bot_`/`vxa_tx_` prefix,
backfill only the prefix-implied scope. Not done — 0.10 tokens were genuinely unscoped (allow-all), the
prefix (where present) was cosmetic, and narrowing on it would be a *narrower* grant than 0.10 gave,
i.e. it could still revoke routes the token legitimately reached. Full-set backfill is the data-correct
mirror of 0.10 behavior.

### Widening caveat

If any deployment intentionally minted unscoped tokens as a **deny-all**, this backfill would widen them
to allow-all. None are known in the 0.10 line, where unscoped == allow-all. Any deployment that used
empty-scope as deny-all must re-scope those rows explicitly before upgrading.

## Validation

`tests/test_stack_postgres.py::test_backfill_grandfathers_empty_token_scopes` (testcontainers-postgres):
seeds a `scopes='{}'` token and a `scopes={tx}` token, re-runs `ensure_schema_sync`, asserts the empty
row converges to `{bot,tx,browser}` while the scoped row is untouched, and that a further run is a no-op.
Red before the backfill (empty row stays `{}`), green after.

## Rollback

Revert the code (`_backfill_token_scopes` and its call site). The backfilled rows are **not** auto-reversed
— prior values were empty (no information to restore), and reverting would re-introduce the 403. If a hard
revert is required it must be reconstructed from a pre-upgrade DB backup.
