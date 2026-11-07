#!/usr/bin/env python3
"""Witness stage 1/2: the full invite→bot→webhook→summary→email loop on the REAL clock against
doubles, narrated. Exit code 0 = every check held; the human's job is only to look."""
from __future__ import annotations

import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows import SystemClock, admit, escalate, reclaim, status, tick  # noqa: E402
from flows_steps.fakes import INVITE_RECEIVED, MEETING_COMPLETED  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from loopback import LoopbackWorld  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
from flows_steps.fakes import build_registry  # noqa: E402

MAILPIT = ("localhost", 1025)
OUTBOX = Path(__file__).resolve().parent / "outbox"


def say(msg: str) -> None:
    print(f"  {time.strftime('%H:%M:%S')} · {msg}", flush=True)


class EmailDouble:
    """Mailpit when it answers, .eml files otherwise — either way a receipt-worthy delivery."""

    def __init__(self) -> None:
        self.mode = "file"
        try:
            with smtplib.SMTP(*MAILPIT, timeout=1):
                self.mode = "mailpit"
        except OSError:
            OUTBOX.mkdir(exist_ok=True)
        say(f"email double: {self.mode}" + ("" if self.mode == "mailpit" else f" → {OUTBOX}"))

    def send(self, to: str, subject: str, body: str) -> str:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "vexa@bank.com", to, subject
        m.set_content(body)
        if self.mode == "mailpit":
            with smtplib.SMTP(*MAILPIT, timeout=5) as s:
                s.send_message(m)
            return f"mailpit:{to}"
        f = OUTBOX / f"{int(time.time()*1000)}-{to}.eml"
        f.write_bytes(bytes(m))
        return str(f)


def main() -> int:
    print("── witness: flows loop on the real clock, doubles for the world ──")
    db, clock, world = SqliteDB(), SystemClock(), LoopbackWorld(duration_s=6.0, webhook_redeliveries=2)
    reg = build_registry(world)
    mailer = EmailDouble()

    # REAL email side-effect replaces the fake's list-append — the witness watches actual delivery
    def confirm_by_email(ctx):
        from flows import Done
        ref = mailer.send(ctx.refs["inviter"], "Vexa will join your meeting",
                          "Vexa joins at start time. Reply to adjust.")
        world.emails.append((ctx.refs["inviter"], "confirm"))
        return Done({"message_id": ref}, provider_ref=ref)

    def email_participants(ctx):
        from flows import Done
        sha = ctx.prior["commit_summary"]["commit_sha"]
        sent = []
        for r in [p for p in ctx.refs["participants"] if p.endswith("@bank.com")]:
            ref = mailer.send(r, "Meeting summary", f"Summary committed: {sha}\nOpen chat: /?meeting={ctx.refs['meeting']}")
            world.emails.append((r, sha))
            sent.append(ref)
        return Done({"sent": sent})

    reg.steps["confirm_by_email"] = confirm_by_email
    reg.steps["email_participants"] = email_participants

    inner = reg.steps["dispatch_bot"]
    def dispatch_bot(ctx):
        out = inner(ctx)
        world.on_dispatch(ctx.refs["meeting"], ctx.refs, ctx.clock_now)
        say(f"bot dispatched → transcribing for {world.duration_s:.0f}s (the double)")
        return out
    reg.steps["dispatch_bot"] = dispatch_bot

    s = reg.steps
    reg.flow(name="invite_to_bot", version=1, on=INVITE_RECEIVED,
             steps=[s["create_meeting"], s["confirm_by_email"], s["await_start"], s["dispatch_bot"]])
    reg.flow(name="post_meeting", version=1, on=MEETING_COMPLETED,
             steps=[s["await_completion"], s["process_transcript"], s["commit_summary"], s["email_participants"]])

    refs = {"meeting": "witness-1", "inviter": "anna@bank.com",
            "participants": ["anna@bank.com", "ben@bank.com", "eve@other.io"],
            "start_time": clock.now() + 4.0}
    say("FACT admitted: invite.received (vexa@bank.com invited; meeting starts in 4s)")
    admit(db, reg, clock, source_event_id="wit-inv-1", event_type="invite.received", subject_refs=refs)

    seen: set = set()
    deadline = time.time() + 60
    while time.time() < deadline:
        reclaim(db, clock); escalate(db, clock)
        fired = world.pump(db, reg, clock)
        if fired:
            say("WORLD: meeting ended, transcript final → webhook meeting.completed (delivered 3×)")
        worked = tick(db, reg, clock)
        for rid, flow, step, st in db.execute("SELECT reaction_id, flow, step, status FROM reaction"):
            k = (rid, step, st)
            if k not in seen:
                seen.add(k)
                say(f"reaction {flow:<14} step={step:<20} status={st}")
        rows = db.execute("SELECT status FROM reaction")
        if rows and all(r[0] in ("done", "failed", "cancelled") for r in rows) and not world._pending:
            break
        if not worked:
            time.sleep(0.25)

    print("\n── checks ──")
    ok = True
    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {name}")
        ok = ok and cond

    ends = {f: st for f, st in db.execute("SELECT flow, status FROM reaction")}
    check("both flows done", ends == {"invite_to_bot": "done", "post_meeting": "done"})
    check("bot dispatched exactly once", world.bots_dispatched == ["witness-1"])
    check("one commit", world.commits == ["sha-witness-1"])
    check("confirm + 2 summary mails, none to the outsider",
          ("anna@bank.com", "confirm") in world.emails
          and ("anna@bank.com", "sha-witness-1") in world.emails
          and ("ben@bank.com", "sha-witness-1") in world.emails
          and not any(r == "eve@other.io" for r, _ in world.emails))
    check("no duplicate emails despite 3× webhook", len(world.emails) == len(set(world.emails)))
    rid = db.execute("SELECT reaction_id FROM reaction WHERE flow='post_meeting'")[0][0]
    receipts = status(db, rid)["receipts"]
    check("every step receipted+confirmed", all(r["state"] == "confirmed" for r in receipts))

    print("\nHUMAN: open http://localhost:8025 — expect 1 confirm (anna) + 2 summaries (anna, ben),"
          "\n       each summary citing sha-witness-1. Nothing for eve@other.io."
          if EmailDouble.__name__ and mailer.mode == "mailpit"
          else f"\nHUMAN: inspect the .eml files in {OUTBOX}")
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
