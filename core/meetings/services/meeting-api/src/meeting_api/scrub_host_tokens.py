"""``python -m meeting_api.scrub_host_tokens`` — remove stored Zoom host tokens (``zak``) from meetings.

A host's calendar carries Zoom START links (``…?zak=<token>``): the token starts the meeting as its
host. Calendar sync, ``POST /bots`` and the planned-meeting routes now drop it on the way in
(``meeting_url_redaction.strip_host_tokens``); rows written before that still hold it, in
``meetings.data`` — ``constructed_meeting_url``, the calendar snapshot under ``calendar_sources``, a
bot's ``reason`` / ``last_error`` / ``status_transition`` / ``join_evidence`` that quoted the link.

This is the out-of-band ops step for those rows (MIGRATION-0012). It touches ONE column,
``meetings.data``, and inside it only the strings whose URLs carry a host-only parameter, each
replaced in place with ``jsonb_set``: every other value keeps its stored bytes, every other byte of
those strings stays, and no other column — not ``updated_at`` — is written.

* Default is a DRY RUN: it counts the rows that carry a host token and the top-level ``data`` keys
  they sit under, and writes nothing.
* ``--apply`` rewrites them, in batches, each row under its own row lock.
* Idempotent: a second run finds nothing to do (the selection is the same pattern the rewrite
  removes, and ``strip_host_tokens`` is idempotent).

The output names counts and keys only, never a token or a URL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from .meeting_url_redaction import strip_host_tokens_in

log = logging.getLogger("meeting_api.scrub_host_tokens")

#: Postgres regex selecting a stored ``zak`` parameter: after ``?``, ``&``, ``#`` or ``&amp;``.
HOST_TOKEN_PATTERN = r"[?&#;]zak="

_COUNT = """
SELECT count(*) FROM meetings WHERE data::text ~* :pattern
"""
_KEYS = """
SELECT e.key, count(*) FROM meetings m, jsonb_each(m.data) e
WHERE m.data::text ~* :pattern AND e.value::text ~* :pattern
GROUP BY e.key ORDER BY e.key
"""
_BATCH = """
SELECT id, data::text FROM meetings WHERE data::text ~* :pattern AND id > :after
ORDER BY id LIMIT :limit FOR UPDATE
"""
#: One string, at one path, replaced in place: every other value keeps its stored bytes.
_UPDATE = """
UPDATE meetings SET data = jsonb_set(data, CAST(:path AS text[]), to_jsonb(CAST(:value AS text)), false)
WHERE id = :id
"""


def changes(value: Any, path: "tuple[str, ...]" = ()) -> "list[tuple[list[str], str]]":
    """Every string inside ``value`` whose URLs carry a host-only parameter, as
    ``(jsonb path, the string without it)``."""
    if isinstance(value, str):
        clean = strip_host_tokens_in(value)
        return [(list(path), clean)] if clean != value else []
    if isinstance(value, list):
        return [c for i, v in enumerate(value) for c in changes(v, path + (str(i),))]
    if isinstance(value, dict):
        return [c for k, v in value.items() for c in changes(v, path + (str(k),))]
    return []


async def survey(conn) -> dict:
    """Read-only: how many rows carry a host token, and under which top-level ``data`` keys."""
    from sqlalchemy import text

    rows = (await conn.execute(text(_COUNT), {"pattern": HOST_TOKEN_PATTERN})).scalar_one()
    keys = {k: n for k, n in (await conn.execute(text(_KEYS), {"pattern": HOST_TOKEN_PATTERN})).all()}
    return {"rows": int(rows), "keys": keys}


async def apply(engine, *, batch: int = 200) -> int:
    """Rewrite every row that carries a host token; returns how many rows were changed."""
    from sqlalchemy import text

    changed, after = 0, 0
    while True:
        async with engine.begin() as conn:
            rows = (await conn.execute(text(_BATCH), {"pattern": HOST_TOKEN_PATTERN,
                                                      "after": after, "limit": batch})).all()
            if not rows:
                return changed
            for row_id, raw in rows:
                after = row_id
                edits = changes(json.loads(raw))
                for path, value in edits:
                    await conn.execute(text(_UPDATE), {"id": row_id, "path": path, "value": value})
                changed += 1 if edits else 0


async def run(database_url: str, *, do_apply: bool, batch: int = 200) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            before = await survey(conn)
        report: dict = {"mode": "apply" if do_apply else "dry-run", "before": before}
        if do_apply:
            report["changed_rows"] = await apply(engine, batch=batch)
            async with engine.connect() as conn:
                report["after"] = await survey(conn)
        return report
    finally:
        await engine.dispose()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meeting_api.scrub_host_tokens",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the rows (default: dry run, counts only)")
    parser.add_argument("--batch", type=int, default=200)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[scrub-host-tokens] %(message)s")
    from .__main__ import _database_url

    report = asyncio.run(run(_database_url(), do_apply=args.apply, batch=args.batch))
    print(json.dumps(report, sort_keys=True))
    if args.apply and report["after"]["rows"]:
        log.error("STOP: %d row(s) still carry a host token", report["after"]["rows"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
