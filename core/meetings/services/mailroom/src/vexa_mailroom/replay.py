"""Replay the fixture corpus through the base path into a real inbox.

The P1 witness harness: every ``*.ics`` in a directory is wrapped as a received message, parsed,
planned by :mod:`.base_path`, and the planned mail is actually sent over SMTP — normally to a
local Mailpit — so a human can read the result as an inbox rather than a log. Negative fixtures
exercise the refusal path and send nothing.

    python -m vexa_mailroom.replay --ics-dir <dir> --org-domain example.com \
        --assistant mk-dev@dev.vexa.ai --smtp localhost:1025 --run-log run-log.jsonl

Only ``METHOD:REQUEST`` messages fan out (an update replans idempotently; a cancel is not a
meeting held). The run log is one JSON line per decision, suppressions included.
"""
from __future__ import annotations

import argparse
import json
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from .base_path import plan_base_path
from .invite import parse_invite

CANNED_SUMMARY = (
    "What changed in this meeting:\n"
    "- Decided: ship the pilot Tuesday; Priya owns the rollback plan.\n"
    "- You committed to: sending the pricing sheet by Friday.\n"
    "- Owed to you: Tomás delivers the load-test numbers before Thursday."
)


def _as_received(ics: bytes, assistant: str) -> bytes:
    return (
        b"MIME-Version: 1.0\r\nMessage-ID: <replay@fixtures>\r\nFrom: replay@fixtures\r\n"
        b"To: " + assistant.encode() + b"\r\nSubject: replayed invite\r\n"
        b"Content-Type: text/calendar; method=REQUEST\r\n\r\n" + ics
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ics-dir", required=True, type=Path)
    ap.add_argument("--org-domain", required=True)
    ap.add_argument("--assistant", required=True)
    ap.add_argument("--smtp", default="localhost:1025")
    ap.add_argument("--sender", default=None, help="From: address (default: the assistant)")
    ap.add_argument("--run-log", type=Path, default=Path("run-log.jsonl"))
    args = ap.parse_args(argv)

    host, _, port = args.smtp.partition(":")
    sender = args.sender or args.assistant
    sent = suppressed = refused = 0

    with args.run_log.open("w", encoding="utf-8") as log, \
         smtplib.SMTP(host, int(port or 25)) as smtp:
        for path in sorted(args.ics_dir.glob("*.ics")):
            parsed = parse_invite(_as_received(path.read_bytes(), args.assistant))
            if parsed.ok and (parsed.method or "").upper() != "REQUEST":
                log.write(json.dumps({"fixture": path.name, "decision": "skip",
                                      "reason": f"method {parsed.method}"}) + "\n")
                continue
            result = plan_base_path(parsed, org_domain=args.org_domain,
                                    assistant=args.assistant,
                                    transcript_summary=CANNED_SUMMARY)
            for plan in result.sends:
                msg = EmailMessage()
                msg["From"], msg["To"], msg["Subject"] = sender, plan.to, plan.subject
                msg.set_content(plan.body)
                smtp.send_message(msg)
                sent += 1
            for e in result.log:
                log.write(json.dumps({"fixture": path.name, **e}) + "\n")
                if e["decision"] == "suppress":
                    suppressed += 1
                    if "rejected" in e["reason"]:
                        refused += 1

    print(f"sent={sent} suppressed={suppressed} (of which refused invites={refused}) "
          f"log={args.run_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
