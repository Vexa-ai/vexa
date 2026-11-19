# MIGRATION-0004 — backfill empty API-token scopes (0.10 → 0.12 grandfather)

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_backfill_token_scopes`),
after the additive column sync and before the index sync. Unlike MIGRATION-0001..0003 (out-of-band,
human-run ops steps), this one runs in the convergence that **adds** `api_tokens.scopes` to a 0.10-era table, because the
failure it prevents is a silent, deploy-time-invisible revocation of every pre-existing customer token —
it must converge at cutover with no operator action. Once the column exists it never runs again. Closes #578.

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

In the same transaction as the `ADD COLUMN`, and only when this convergence is the one adding it:

```sql
UPDATE api_tokens SET scopes = '{bot,tx,browser}' WHERE scopes = '{}'::text[];
```

Full valid-scope set = mirrors 0.10's unscoped allow-all behavior, and makes the DB self-describing (the
`/internal/validate` path stays simple — no read-time remapping of empty/`legacy`). Every row it touches
existed before the column did, so:

- a row stored with `scopes = '{}'` after the column exists is a key with **no access** (the read path
  denies it) and is **never rewritten** — not on a restart, not on an upgrade;
- already-scoped rows (a token deliberately minted `{tx}`) are **never widened**;
- re-running `ensure_schema` is a no-op (the column exists).

`scopes` is `NOT NULL`, so a NULL row cannot exist.

### Scope-prefix refinement — deliberately NOT taken

The issue floats an optional refinement: where the token value carries a `vxa_bot_`/`vxa_tx_` prefix,
backfill only the prefix-implied scope. Not done — 0.10 tokens were genuinely unscoped (allow-all), the
prefix (where present) was cosmetic, and narrowing on it would be a *narrower* grant than 0.10 gave,
i.e. it could still revoke routes the token legitimately reached. Full-set backfill is the data-correct
mirror of 0.10 behavior.

### Widening caveat

Only rows from a table that had no `scopes` column are widened: on 0.10 those keys were unscoped, which
was allow-all. A key stored with empty scopes on 0.12 or later had no access and keeps none.

## Validation

- `tests/test_stack_postgres.py::test_backfill_grandfathers_pre_scope_tokens`: an `api_tokens` table
  without `scopes` converges to `{bot,tx,browser}` on every row; a further run changes nothing.
- `tests/test_stack_postgres.py::test_an_empty_scope_token_is_never_widened`: a `{}` row and a `{tx}` row
  are unchanged by two convergences.
- `tests/test_token_scope_backfill_pg.py`: a `{}` key gets 403 from `get_current_user` and `["legacy"]`
  from `/internal/validate` before and after a convergence; a NULL row is refused by the column; a
  pre-scope key gets 200 and the full set.

## Rollback

Revert the code (`_backfill_token_scopes` and its call site). The backfilled rows are **not** auto-reversed
— prior values were empty (no information to restore), and reverting would re-introduce the 403. If a hard
revert is required it must be reconstructed from a pre-upgrade DB backup.
