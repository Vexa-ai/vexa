"""The v0 trigger: run the pipeline over named meetings from the command line.

```
PYTHONPATH=src:../../modules/presend-gate/src uv run python -m vexa_artifact_pipeline \
  --meeting 12615 --creator "Dmitry Grankin" --bot-name "Vexa test" \
  --base-url http://127.0.0.1:18056 --out out/artifacts --run-log out/runs.jsonl
```

The real trigger is the ``meeting.completed`` webhook. This is the same
:class:`~vexa_artifact_pipeline.ports.MeetingSource` port with a human typing the ids, so
what runs here is what the webhook will run.

Nothing here prints a secret. ``--api-key`` is read from the environment by default and its
value is never echoed; the magic-link signing key is never touched at all — it belongs to
the postman, which inherits it from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .delivery import FileDelivery, NullDelivery, postman_delivery
from .directory import RosterDirectory
from .gateway import CorpusTransport, HttpMeetingGateway
from .pipeline import ArtifactPipeline, ListSource, RunResult
from .ports import CompletedMeeting
from .runlog import JsonlRunLog


def _address_book(pairs: list[str]) -> dict[str, str]:
    book: dict[str, str] = {}
    for pair in pairs:
        name, sep, email = pair.partition("=")
        if not sep or not name.strip() or not email.strip():
            raise SystemExit(f"--address expects 'Display Name=email@host', got {pair!r}")
        book[name.strip()] = email.strip()
    return book


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m vexa_artifact_pipeline",
        description="Turn completed meetings into gated, per-participant context deltas.",
    )
    ap.add_argument("--meeting", action="append", required=True, metavar="ID",
                    help="a completed meeting id (repeatable)")
    ap.add_argument("--base-url", default=os.environ.get("VEXA_API_URL", "http://127.0.0.1:18056"),
                    help="meeting API origin (default: VEXA_API_URL)")
    ap.add_argument("--api-key", default=os.environ.get("VEXA_API_KEY"),
                    help="API key sent as X-API-Key (default: VEXA_API_KEY; never echoed)")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="read records from a directory of harvested record JSON instead of "
                         "the API — a dev source, labelled as such in the run log")
    ap.add_argument("--creator", default=None, help="who convened the meetings")
    ap.add_argument("--creator-email", default=None)
    ap.add_argument("--bot-name", action="append", default=[],
                    help="the bot's display name in the meeting UI (repeatable)")
    ap.add_argument("--address", action="append", default=[], metavar="NAME=EMAIL",
                    help="address-book entry (repeatable)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write artifacts to this directory (file sink)")
    ap.add_argument("--postman", type=Path, default=None, metavar="CHAT_DOOR_DIR",
                    help="deliver by email through the chat-door postman at this package dir")
    ap.add_argument("--door-base-url", default=os.environ.get("CHAT_DOOR_BASE_URL", "http://127.0.0.1:8080"))
    ap.add_argument("--smtp-host", default="127.0.0.1")
    ap.add_argument("--smtp-port", type=int, default=1025)
    ap.add_argument("--from-addr", default=None)
    ap.add_argument("--run-log", type=Path, default=Path("runs.jsonl"))
    ap.add_argument("--include-artifacts", action="store_true",
                    help="embed full artifact bodies in the run log (meeting content — off "
                         "by default; use for fixture runs, not for a shared log)")
    ap.add_argument("--json", action="store_true", help="print each run as a JSON line")
    return ap


def _delivery(args: argparse.Namespace):
    if args.postman:
        return postman_delivery(
            args.postman,
            base_url=args.door_base_url,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            from_addr=args.from_addr,
            python=sys.executable,
        )
    if args.out:
        return FileDelivery(args.out)
    return NullDelivery()


def _summarize(result: RunResult) -> str:
    counts: dict[str, int] = {}
    for outcome in result.outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    tally = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no participants"
    drift = "" if result.id_matches_request else f" (requested {result.requested_id})"
    reasons = ",".join(result.reasons)
    return f"record {result.meeting_id}{drift} · {result.verdict} [{reasons}] · {tally}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    transport = CorpusTransport(args.corpus) if args.corpus else None
    gateway = HttpMeetingGateway(args.base_url, api_key=args.api_key, transport=transport)
    pipeline = ArtifactPipeline(
        gateway=gateway,
        directory=RosterDirectory(address_book=_address_book(args.address)),
        delivery=_delivery(args),
        run_log=JsonlRunLog(args.run_log),
        source=ListSource(
            CompletedMeeting(
                meeting_id=str(m),
                creator=args.creator,
                creator_email=args.creator_email,
                bot_names=tuple(args.bot_name),
            )
            for m in args.meeting
        ),
        include_artifacts_in_log=args.include_artifacts,
    )
    try:
        results = pipeline.drain()
    finally:
        gateway.close()

    for result in results:
        if args.json:
            print(json.dumps(result.to_log_entry(include_artifacts=args.include_artifacts),
                             ensure_ascii=False))
        else:
            print(_summarize(result))
    if args.corpus:
        print(f"note: records were read from {args.corpus} (dev source), not from the meeting API",
              file=sys.stderr)
    return 0 if all(r.found for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
