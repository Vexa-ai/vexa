# MIGRATION-0007 — `ix_users_email_lower` (case-folded email lookups) and the UNIQUE upgrade

**Status:** the NON-UNIQUE expression index is in the SSOT model (`schema/models.py`) and ships with
this release; `ensure_schema` builds it on a fresh DB and adds it additively on an existing one. The
**UNIQUE** version — the one that actually enforces one-address-one-account — is an **out-of-band,
human-run ops step that must be preceded by reconciling duplicate rows**. It is deliberately NOT in
the model. Read "Why it is not unique yet" before deciding to add it.

## What the index is

```sql
CREATE INDEX ix_users_email_lower ON users (lower(email));
```

## Why it exists

Every email lookup in admin-api folds case (R-B08):

- `POST /admin/users` — `select(User).where(func.lower(User.email) == user_in.email.lower())`
- `GET /admin/users/email/{email}` — the same predicate

The plain `users.email` unique index cannot serve `lower(email) = …`, so both lookups were
sequential scans on `users` — on the sign-in path, which is every person entering the product.

## Why it is not unique yet

The invariant we want is **one address, one account**, and its DB form is
`CREATE UNIQUE INDEX … ON users (lower(email))`. It cannot ship as part of this release:

1. **The instances that need it are the instances that break it.** Case-variant duplicate rows are
   the defect this fold exists to stop; an instance that ran the exact-match code has them. A
   `CREATE UNIQUE INDEX` against those rows raises `UniqueViolation`.
2. **`_sync_indexes` fails closed on a unique index it cannot build** (`schema/sync.py`, #1186) —
   deliberately: a DB whose duplicates block a load-bearing unique index must not be served by a
   process that assumes the index exists. So a unique `lower(email)` in the model turns "this
   instance has a few ghost accounts" into "admin-api will not start". **Trading a data defect for
   an outage is not a fix.**
3. **`ensure_schema` runs inside one transaction**, so it cannot use `CONCURRENTLY`; a plain
   `CREATE UNIQUE INDEX` on `users` blocks writes for the build.

## What closes the hole in the meantime

- **New rows are stored folded** (`app/main.normalise_email`, `create_user`). A second create in a
  different case now collides with the **`users.email` UNIQUE index that has always existed**, and
  `create_user` catches the `IntegrityError` and re-resolves to the winning row (200, not 500 and
  not a second account). This is what closes the two-concurrent-creates race the read-side fold
  could not.
- **Both lookups `ORDER BY users.id`** — oldest wins. An instance that already holds duplicates
  resolves the *same* row from both routes, every time, instead of whichever row the plan reached
  first. Deterministic-wrong is recoverable; nondeterministic-wrong puts the desk on one account and
  the meetings on another.
- **Existing rows are never rewritten.** An existing address's case is the case its person typed and
  the case their mail already goes to; rewriting the table to suit an index is changing data to suit
  a query plan.

## The UNIQUE upgrade (operator, out of band, in this order)

Run against the target DB **as standalone statements** (psql, not wrapped in a `BEGIN`).

**1. Find the duplicates.** If this returns nothing, skip to step 3.

```sql
SELECT lower(email) AS addr, count(*), array_agg(id ORDER BY id) AS ids
FROM users GROUP BY lower(email) HAVING count(*) > 1;
```

**2. Reconcile them.** This is a **judgement call, not a script**: for each group, the oldest id is
the account the application now resolves to (both lookups `ORDER BY id`), so the question is what
the newer rows carry — API tokens, meetings, memberships, `data.is_admin`, an onboarding stamp.
Move what matters onto the oldest row, then remove the extras. Nothing here is reversible; take a
backup first.

**3. Build the index concurrently.**

```sql
CREATE UNIQUE INDEX CONCURRENTLY uq_users_email_lower ON users (lower(email));
```

`CONCURRENTLY` does not take the write lock; if it fails it leaves an `INVALID` index behind — drop
it (`DROP INDEX uq_users_email_lower;`) and go back to step 1.

**4. Only then** may `models.py` carry `Index("uq_users_email_lower", text("lower(email)"),
unique=True)` — and only for an estate where step 3 has been done everywhere, because from that
commit on, any instance still holding duplicates refuses to start.

## Rollback

Dropping either index is safe and instant (`DROP INDEX [CONCURRENTLY] ix_users_email_lower;`) — the
lookups fall back to a sequential scan and remain correct. Nothing in the application reads the
index by name.
