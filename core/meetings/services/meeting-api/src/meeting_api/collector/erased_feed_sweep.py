"""One-time operator sweep: erase the Redis transcript keys of meetings whose transcript was deleted.

The delete route erases a meeting's Redis keys itself (``erased_meeting_cache_keys``). Meetings
deleted by a release that predates that step still hold their live transcript feed
(``tc:meeting:{id}``) in Redis. Every reader refuses a meeting carrying the deletion stamp, so the
feed is unreadable through the product, but it stays at rest until removed. This sweep removes it:
for every meeting row with ``data.artifact_deletion`` it deletes the same keys the delete route does
and drops the id from ``active_meetings``. Idempotent; safe to re-run; touches no other meeting.

Run it once after upgrading, from any meeting-api container (same env as the service):

    python -m meeting_api.collector.erased_feed_sweep --dry-run   # count only
    python -m meeting_api.collector.erased_feed_sweep             # delete

It prints one JSON line: how many deleted meetings were found, how many still had keys, how many
keys were removed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Optional

from .db_writer import ACTIVE_MEETINGS_KEY
from .ports import erased_meeting_cache_keys


async def sweep_erased_feeds(store, redis_client, *, dry_run: bool = False) -> dict:
    """Delete the Redis transcript keys of every meeting ``store`` reports as erased.

    ``store.erased_meeting_ids()`` names the meetings (rows carrying ``data.artifact_deletion``);
    ``redis_client`` is an async Redis client. With ``dry_run`` nothing is deleted and the counts
    say what would be."""
    ids = list(await store.erased_meeting_ids())
    with_keys = 0
    keys_found = 0
    for meeting_id in ids:
        keys = erased_meeting_cache_keys(meeting_id)
        present = int(await redis_client.exists(*keys))
        if present:
            with_keys += 1
            keys_found += present
        if not dry_run:
            if present:
                await redis_client.delete(*keys)
            await redis_client.srem(ACTIVE_MEETINGS_KEY, str(meeting_id))
    return {"erased_meetings": len(ids), "meetings_with_keys": with_keys,
            ("keys_found" if dry_run else "keys_deleted"): keys_found, "dry_run": dry_run}


def _database_url() -> str:
    from ..__main__ import _database_url as service_database_url

    return service_database_url()


async def _run(dry_run: bool) -> dict:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ..db import build_engine
    from .adapters import SqlAlchemyTranscriptStore

    engine = build_engine(_database_url())
    redis_client = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, health_check_interval=30)
    try:
        store = SqlAlchemyTranscriptStore(async_sessionmaker(engine, expire_on_commit=False))
        return await sweep_erased_feeds(store, redis_client, dry_run=dry_run)
    finally:
        await redis_client.aclose()
        await engine.dispose()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m meeting_api.collector.erased_feed_sweep",
        description="Erase the Redis transcript keys of meetings whose transcript was deleted.")
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted; delete nothing")
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(_run(args.dry_run))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
