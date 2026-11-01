"""`python -m admin_api.prune_expired_keys` — a one-off: delete API keys that have already expired (#1868).

Before 0.13.4, Flows minted a 15-minute `bot,browser,tx` key on the user's account for its gateway reads
and cached it per process only, so every restart created another one. Hundreds of expired keys built up
in people's key lists. 0.13.4 stops the minting, and the key list no longer shows expired keys. This
removes the rows already there.

What it touches: rows of `api_tokens` with `kind = 'key'` whose `expires_at` is set and earlier than the
cutoff: `--before <ISO UTC>` when given, else now minus `--grace-hours` (default 1). Never a key without
an expiry, never a key that is still valid (a cutoff in the future is refused), never a session
(`kind = 'session'`). An expired key cannot authenticate (`app/validate.py` refuses it), so deleting one
changes no access; it removes the row and its metadata.

Default is a DRY RUN: it prints the cutoff it used, how many rows match, over how many accounts, the
expiry range, a breakdown by key name and scopes, and the ten accounts with the most (user ids only).
Key names held by fewer than 3 accounts are folded into one "(other names)" line, so a name a person
typed is not printed. It never prints a token value or an email.

    python -m admin_api.prune_expired_keys                                    # dry run, prints the cutoff
    python -m admin_api.prune_expired_keys --apply --max 5000 --before 2026-11-01T09:00:00Z

`--apply` requires `--max`: the number from the dry run that the operator agreed to delete. When more
rows match than that, it deletes nothing and exits 2; otherwise it deletes at most `--max` rows in total,
even if more rows come to match while it runs, and says when it stopped at the cap. Pass the dry run's
`--before` to `--apply` so both use the same cutoff. Deletion runs in batches of `--batch` rows (one
transaction each) and stops at the first error. Exit 0 when done (or nothing matched).
Reads the database from the same `DATABASE_URL` / `DB_*` env as the service.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from .schema.models import APIToken


# A key name shown in the breakdown must be held by at least this many accounts: names the software
# minted (the same on every account) show; a name one person typed folds into "(other names)".
NAME_MIN_ACCOUNTS = 3
OTHER_NAMES = "(other names)"


def parse_before(value: str) -> datetime:
    """An ISO-8601 instant → naive UTC (the column's form). A value without an offset is read as UTC."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _expired(cutoff: datetime):
    return (APIToken.kind == "key") & APIToken.expires_at.is_not(None) & (APIToken.expires_at < cutoff)


async def survey(session_factory, cutoff: datetime) -> dict:
    async with session_factory() as s:
        n, users, oldest, newest = (await s.execute(
            select(func.count(APIToken.id), func.count(func.distinct(APIToken.user_id)),
                   func.min(APIToken.expires_at), func.max(APIToken.expires_at)).where(_expired(cutoff))
        )).one()
        top = (await s.execute(
            select(APIToken.user_id, func.count(APIToken.id).label("n")).where(_expired(cutoff))
            .group_by(APIToken.user_id).order_by(func.count(APIToken.id).desc()).limit(10)
        )).all()
    return {"rows": int(n or 0), "accounts": int(users or 0), "oldest": oldest, "newest": newest,
            "top": [(int(u), int(k)) for u, k in top]}


async def breakdown(session_factory, cutoff: datetime) -> list[tuple[str, str, int, int | None]]:
    """Matching rows grouped by (key name, scopes): [(name label, scopes, rows, accounts or None)].
    Names held by fewer than NAME_MIN_ACCOUNTS accounts fold into OTHER_NAMES (accounts not counted)."""
    async with session_factory() as s:
        common = set((await s.execute(
            select(APIToken.name).where(_expired(cutoff)).group_by(APIToken.name)
            .having(func.count(func.distinct(APIToken.user_id)) >= NAME_MIN_ACCOUNTS)
        )).scalars().all())
        rows = (await s.execute(
            select(APIToken.name, APIToken.scopes, func.count(APIToken.id),
                   func.count(func.distinct(APIToken.user_id)))
            .where(_expired(cutoff)).group_by(APIToken.name, APIToken.scopes)
        )).all()
    agg: dict[tuple[str, str], list] = {}
    for name, scopes, n, users in rows:
        shown = name in common
        label = ("(no name)" if name is None else repr(name)) if shown else OTHER_NAMES
        key = (label, ",".join(sorted(scopes or [])))
        cur = agg.setdefault(key, [0, 0 if shown else None])
        cur[0] += int(n)
        if shown:
            cur[1] += int(users)
    return sorted(((lb, sc, v[0], v[1]) for (lb, sc), v in agg.items()), key=lambda r: (-r[2], r[0], r[1]))


async def prune(session_factory, cutoff: datetime, batch: int, limit: int) -> int:
    """Delete matching rows in batches; stop after `limit` rows in total, even if more come to match."""
    deleted = 0
    while deleted < limit:
        async with session_factory() as s:
            ids = (await s.execute(
                select(APIToken.id).where(_expired(cutoff)).order_by(APIToken.id)
                .limit(min(batch, limit - deleted))
            )).scalars().all()
            if not ids:
                return deleted
            res = await s.execute(delete(APIToken).where(APIToken.id.in_(ids)).where(_expired(cutoff)))
            await s.commit()
            deleted += res.rowcount or 0
    return deleted


async def run(url: str, apply: bool, max_rows: int | None, grace_hours: float, batch: int,
              out=print, before: datetime | None = None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.utcnow()
        # Whole seconds, so the cutoff printed is exactly the one used and can be passed back as --before.
        cutoff = before if before is not None else (now - timedelta(hours=grace_hours)).replace(microsecond=0)
        stamp = cutoff.isoformat() + "Z"
        if cutoff > now:
            out(f"STOP: cutoff {stamp} is in the future; it would match keys that are still valid; nothing deleted")
            return 2
        info = await survey(factory, cutoff)
        out(f"expired keys (kind=key, expired before {stamp}): "
            f"{info['rows']} rows on {info['accounts']} accounts; expiries {info['oldest']} .. {info['newest']}")
        out(f"cutoff: {stamp} (pass --before {stamp} to --apply to use the same cutoff)")
        out(f"by key name and scopes (names on fewer than {NAME_MIN_ACCOUNTS} accounts shown as {OTHER_NAMES}):")
        for label, scopes, k, users in await breakdown(factory, cutoff):
            out(f"  {label} [{scopes}]: {k} rows" + (f" on {users} accounts" if users is not None else ""))
        out("top accounts:")
        for uid, k in info["top"]:
            out(f"  user {uid}: {k}")
        if not apply:
            out("dry run: nothing deleted (pass --apply --max N to delete)")
            return 0
        if info["rows"] > max_rows:
            out(f"STOP: {info['rows']} rows match, more than --max {max_rows}; nothing deleted")
            return 2
        n = await prune(factory, cutoff, batch, limit=max_rows)
        left = (await survey(factory, cutoff))["rows"]
        capped = f" (stopped at --max {max_rows})" if n >= max_rows and left else ""
        out(f"deleted {n} expired keys{capped}; {left} still match")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m admin_api.prune_expired_keys", description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    ap.add_argument("--max", type=int, dest="max_rows", help="with --apply: refuse when more rows than this match")
    ap.add_argument("--grace-hours", type=float, default=None,
                    help="only keys expired longer ago than this (default 1); not with --before")
    ap.add_argument("--before", help="cutoff as an ISO UTC instant, e.g. the one the dry run printed; "
                                     "only keys expired before it")
    ap.add_argument("--batch", type=int, default=500, help="rows per delete transaction (default 500)")
    a = ap.parse_args(argv)
    if a.apply and (a.max_rows is None or a.max_rows < 0):
        ap.error("--apply needs --max N (the count from the dry run you agreed to delete)")
    if a.before is not None and a.grace_hours is not None:
        ap.error("pass --before or --grace-hours, not both")
    grace = 1.0 if a.grace_hours is None else a.grace_hours
    if a.batch < 1 or grace < 0:
        ap.error("--batch must be >= 1 and --grace-hours >= 0")
    before = None
    if a.before is not None:
        try:
            before = parse_before(a.before)
        except ValueError:
            ap.error(f"--before {a.before!r} is not an ISO-8601 instant (e.g. 2026-11-01T09:00:00Z)")
    from .__main__ import _database_url  # noqa: PLC0415
    return asyncio.run(run(_database_url(), a.apply, a.max_rows, grace, a.batch, before=before))


if __name__ == "__main__":
    sys.exit(main())
