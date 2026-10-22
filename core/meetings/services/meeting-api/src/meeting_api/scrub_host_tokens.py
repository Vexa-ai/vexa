"""``python -m meeting_api.scrub_host_tokens`` — remove stored Zoom host tokens (``zak``) from meetings,
and mask the passcodes a bot's diagnostics quoted.

Two things written before this release are still in ``meetings.data``:

* **Host tokens.** A host's calendar carries Zoom START links (``…?zak=<token>``): the token starts
  the meeting as its host. Calendar sync, ``POST /bots`` and the planned-meeting routes now drop it
  on the way in (``meeting_url_redaction.strip_host_tokens``); older rows hold it in
  ``constructed_meeting_url``, the calendar snapshot under ``calendar_sources``, and every
  diagnostic that quoted the link. It is REMOVED wherever it is.
* **Passcodes in diagnostics.** A bot that failed on a link quoted it — ``pwd``, ``p``, ``jwt``… —
  in ``reason``, ``last_error``, ``join_evidence`` and ``status_transition``. The bot now redacts
  that text before it reports it; in older rows the values are MASKED (``redact_urls_in``) in
  those four keys only. The join links auto-join reads (``constructed_meeting_url``,
  ``calendar_sources``) keep their attendee passcode, or the meeting could not be joined again.

This is the out-of-band ops step for those rows (MIGRATION-0012). It touches ONE column,
``meetings.data``, and inside it only the strings that change, each replaced in place with
``jsonb_set``: every other value keeps its stored bytes, every other byte of those strings stays,
and no other column — not ``updated_at`` — is written.

* Default is a DRY RUN: it counts the rows of each kind and the top-level ``data`` keys they sit
  under, and writes nothing.
* ``--apply`` rewrites them, in batches, each row under its own row lock.
* Idempotent: a second run finds nothing to do — a masked value is ``***``, which the selection
  does not match, and both rewrites are idempotent.

The output names counts and keys only, never a token, a passcode or a URL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from .meeting_url_redaction import redact_urls_in, strip_host_tokens_in

log = logging.getLogger("meeting_api.scrub_host_tokens")

#: An http(s) URL inside the stored JSON text — the only place either rewrite acts, so the selection
#: is anchored to it (JavaScript in a stored error, ``x?P=…``, is not a link).
_IN_URL = r"https?://[^[:space:]\"'<>`\\]*([?&#]|&amp;)"
#: Postgres regex selecting a stored ``zak`` parameter on a URL (after ``?``, ``&``, ``#`` or ``&amp;``).
HOST_TOKEN_PATTERN = _IN_URL + "zak="
#: Postgres regex selecting an UNMASKED passcode parameter (a masked one reads ``***``).
PASSCODE_PATTERN = _IN_URL + r"(pwd|password|passcode|p|tk|token|jwt)=[^*&#[:space:]\"\\]"
#: The bot's diagnostics: free text that quoted the link, read by people, never joined from.
DIAGNOSTIC_KEYS = ("reason", "last_error", "join_evidence", "status_transition")

_DIAG = "jsonb_build_array(" + ", ".join(f"data->'{k}'" for k in DIAGNOSTIC_KEYS) + ")::text"
_SELECT = f"(data::text ~* :host OR {_DIAG} ~* :passcode)"

_HOST_ROWS = "SELECT count(*) FROM meetings WHERE data::text ~* :host"
_HOST_KEYS = """
SELECT e.key, count(*) FROM meetings m, jsonb_each(m.data) e
WHERE m.data::text ~* :host AND e.value::text ~* :host
GROUP BY e.key ORDER BY e.key
"""
_PASS_ROWS = f"SELECT count(*) FROM meetings WHERE {_DIAG} ~* :passcode"
_PASS_KEYS = f"""
SELECT e.key, count(*) FROM meetings m, jsonb_each(m.data) e
WHERE {_DIAG.replace("data->", "m.data->")} ~* :passcode
  AND e.key IN ({", ".join(f"'{k}'" for k in DIAGNOSTIC_KEYS)}) AND e.value::text ~* :passcode
GROUP BY e.key ORDER BY e.key
"""
_ROWS = f"SELECT count(*) FROM meetings WHERE {_SELECT}"
_BATCH = f"""
SELECT id, data::text FROM meetings WHERE {_SELECT} AND id > :after
ORDER BY id LIMIT :limit FOR UPDATE
"""
#: One string, at one path, replaced in place: every other value keeps its stored bytes.
_UPDATE = """
UPDATE meetings SET data = jsonb_set(data, CAST(:path AS text[]), to_jsonb(CAST(:value AS text)), false)
WHERE id = :id
"""
_PATTERNS = {"host": HOST_TOKEN_PATTERN, "passcode": PASSCODE_PATTERN}


def changes(value: Any, path: "tuple[str, ...]" = (), *, diagnostic: bool = False
            ) -> "list[tuple[list[str], str]]":
    """Every string inside ``value`` that changes, as ``(jsonb path, its new text)``: host tokens
    removed everywhere, passcodes masked under the diagnostic keys."""
    if isinstance(value, str):
        clean = strip_host_tokens_in(value)
        if diagnostic:
            clean = redact_urls_in(clean)
        return [(list(path), clean)] if clean != value else []
    if isinstance(value, list):
        return [c for i, v in enumerate(value)
                for c in changes(v, path + (str(i),), diagnostic=diagnostic)]
    if isinstance(value, dict):
        return [c for k, v in value.items()
                for c in changes(v, path + (str(k),),
                                 diagnostic=diagnostic or (not path and k in DIAGNOSTIC_KEYS))]
    return []


async def survey(conn) -> dict:
    """Read-only: how many rows carry a host token or a diagnostic passcode, and under which keys."""
    from sqlalchemy import text

    rows = (await conn.execute(text(_ROWS), _PATTERNS)).scalar_one()
    host_rows = (await conn.execute(text(_HOST_ROWS), _PATTERNS)).scalar_one()
    host_keys = (await conn.execute(text(_HOST_KEYS), _PATTERNS)).all()
    pass_rows = (await conn.execute(text(_PASS_ROWS), _PATTERNS)).scalar_one()
    pass_keys = (await conn.execute(text(_PASS_KEYS), _PATTERNS)).all()
    return {"rows": int(rows),
            "host_token": {"rows": int(host_rows), "keys": {k: int(n) for k, n in host_keys}},
            "passcode_in_diagnostics": {"rows": int(pass_rows),
                                        "keys": {k: int(n) for k, n in pass_keys}}}


async def apply(engine, *, batch: int = 200) -> int:
    """Rewrite every selected row; returns how many rows were changed."""
    from sqlalchemy import text

    changed, after = 0, 0
    while True:
        async with engine.begin() as conn:
            rows = (await conn.execute(text(_BATCH), {**_PATTERNS, "after": after,
                                                      "limit": batch})).all()
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
        log.error("STOP: %d row(s) still carry a host token or a diagnostic passcode",
                  report["after"]["rows"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
