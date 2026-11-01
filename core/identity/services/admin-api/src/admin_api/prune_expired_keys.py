"""`python -m admin_api.prune_expired_keys` — a one-off: delete API keys that have already expired (#1868).

Before 0.13.4, Flows minted a 15-minute `bot,browser,tx` key on the user's account for its gateway reads
and cached it per process only, so every restart created another one. Hundreds of expired keys built up
in people's key lists. 0.13.4 stops the minting, and the key list no longer shows expired keys. This
removes the rows already there.

What it touches: rows of `api_tokens` with `kind = 'key'` whose `expires_at` is set and passed more than
`--grace-hours` ago (default 1). Never a key without an expiry, never a key that is still valid, never
a session (`kind = 'session'`). An expired key cannot authenticate (`app/validate.py` refuses it), so
deleting one changes no access; it removes the row and its metadata.

Default is a DRY RUN: it prints how many rows match, over how many accounts, the expiry range and the
ten accounts with the most (user ids only). It never prints a token value or an email.

    python -m admin_api.prune_expired_keys                       # dry run
    python -m admin_api.prune_expired_keys --apply --max 5000    # delete, refuse if more than 5000 match

`--apply` requires `--max`: the number from the dry run that the operator agreed to delete. When more
rows match than that, it deletes nothing and exits 2. Deletion runs in batches of `--batch` rows (one
transaction each) and stops at the first error. Exit 0 when done (or nothing matched).
Reads the database from the same `DATABASE_URL` / `DB_*` env as the service.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from .schema.models import APIToken


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


async def prune(session_factory, cutoff: datetime, batch: int) -> int:
    deleted = 0
    while True:
        async with session_factory() as s:
            ids = (await s.execute(
                select(APIToken.id).where(_expired(cutoff)).order_by(APIToken.id).limit(batch)
            )).scalars().all()
            if not ids:
                return deleted
            res = await s.execute(delete(APIToken).where(APIToken.id.in_(ids)).where(_expired(cutoff)))
            await s.commit()
            deleted += res.rowcount or 0


async def run(url: str, apply: bool, max_rows: int | None, grace_hours: float, batch: int,
              out=print) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        cutoff = datetime.utcnow() - timedelta(hours=grace_hours)
        info = await survey(factory, cutoff)
        out(f"expired keys (kind=key, expired before {cutoff.isoformat(timespec='seconds')}Z): "
            f"{info['rows']} rows on {info['accounts']} accounts; expiries {info['oldest']} .. {info['newest']}")
        for uid, k in info["top"]:
            out(f"  user {uid}: {k}")
        if not apply:
            out("dry run: nothing deleted (pass --apply --max N to delete)")
            return 0
        if info["rows"] > max_rows:
            out(f"STOP: {info['rows']} rows match, more than --max {max_rows}; nothing deleted")
            return 2
        n = await prune(factory, cutoff, batch)
        left = (await survey(factory, cutoff))["rows"]
        out(f"deleted {n} expired keys; {left} still match")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m admin_api.prune_expired_keys", description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    ap.add_argument("--max", type=int, dest="max_rows", help="with --apply: refuse when more rows than this match")
    ap.add_argument("--grace-hours", type=float, default=1.0, help="only keys expired longer ago than this (default 1)")
    ap.add_argument("--batch", type=int, default=500, help="rows per delete transaction (default 500)")
    a = ap.parse_args(argv)
    if a.apply and (a.max_rows is None or a.max_rows < 0):
        ap.error("--apply needs --max N (the count from the dry run you agreed to delete)")
    if a.batch < 1 or a.grace_hours < 0:
        ap.error("--batch must be >= 1 and --grace-hours >= 0")
    from .__main__ import _database_url  # noqa: PLC0415
    return asyncio.run(run(_database_url(), a.apply, a.max_rows, a.grace_hours, a.batch))


if __name__ == "__main__":
    sys.exit(main())
