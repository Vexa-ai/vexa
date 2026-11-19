#!/usr/bin/env python3
"""Witness stage 3 — EVERYTHING REAL except the meeting's audio:
fixture ICS invite (specific wall time) → confirm email (Mailpit) → REAL bot joins the REAL
Google Meet at start time (watch it in the call) → fixture transcript injected (this stack runs
no whisper — the one double) → bot stopped, meeting completes → REAL agent worker writes the
meeting note into the real workspace (real model, real git commit) → summary email with the
commit + chat link. Orchestrated end to end by the core/flows engine on the system clock.

Usage: python3 run_real.py --url https://meet.google.com/xxx-yyyy-zzz [--start-in 75]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from flows import Done, EventType, Registry, SystemClock, Wait, admit, status, tick, reclaim, escalate  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
import real_steps as rs  # noqa: E402

INVITE = EventType("invite.received")


def say(msg: str) -> None:
    print(f"  {time.strftime('%H:%M:%S')} · {msg}", flush=True)


def ics_fixture(url: str, start_epoch: float) -> str:
    dt = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(start_epoch))
    return ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:witness-real-1\n"
            f"DTSTART:{dt}\nSUMMARY:Pilot sync (witness)\nLOCATION:{url}\n"
            "ATTENDEE:mailto:anna@bank.com\nATTENDEE:mailto:ben@bank.com\n"
            "ATTENDEE:mailto:vexa@bank.com\nEND:VEVENT\nEND:VCALENDAR\n")


def parse_ics(text: str) -> dict:
    import calendar
    fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    start = calendar.timegm(time.strptime(fields["DTSTART"], "%Y%m%dT%H%M%SZ"))
    attendees = [line.split("mailto:")[1] for line in text.splitlines() if line.startswith("ATTENDEE")]
    return {"url": fields["LOCATION"], "start": start, "title": fields["SUMMARY"],
            "participants": [a for a in attendees if a != "vexa@bank.com"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--start-in", type=float, default=75.0)
    ap.add_argument("--transcribe-s", type=float, default=45.0)
    args = ap.parse_args()

    api_key = Path("/tmp/witness-key").read_text().strip()
    subject = Path("/tmp/witness-uid").read_text().strip()

    print("── witness: REAL bot · REAL agent · fixture invite+transcript ──")
    ics = ics_fixture(args.url, time.time() + args.start_in)
    ev = parse_ics(ics)
    say(f"ICS fixture parsed: '{ev['title']}' at {time.strftime('%H:%M:%S', time.localtime(ev['start']))} → {ev['url']}")

    db, clock, reg = SqliteDB(), SystemClock(), Registry()
    state: dict = {}

    @reg.step
    def confirm_by_email(ctx):
        ref = rs.send_mail(ctx.refs["inviter"],
                           f"Vexa will join: {ctx.refs['title']}",
                           f"Vexa joins {ctx.refs['url']} at "
                           f"{time.strftime('%H:%M', time.localtime(ctx.refs['start']))}.")
        say(f"confirm email → Mailpit ({ctx.refs['inviter']})")
        return Done({"message_id": ref})

    @reg.step
    def await_start(ctx):
        if ctx.clock_now < ctx.refs["start"] - 20:
            say(f"waiting for meeting time ({int(ctx.refs['start'] - ctx.clock_now)}s)…")
            return Wait(until=ctx.refs["start"] - 20)
        return Done({})

    @reg.step
    def dispatch_bot(ctx):
        say("dispatching REAL bot — watch your meeting for 'Vexa Witness' asking to join")
        body = rs.spawn_bot(api_key, ctx.refs["url"])
        state["meeting_id"] = body.get("id")
        state["platform"] = body.get("platform", "google_meet")
        state["native"] = body.get("native_meeting_id") or ctx.refs["url"].rsplit("/", 1)[1]
        say(f"meeting row {state['meeting_id']} · bot container spawning (docker ps: vexa-bot)")
        return Done({"meeting_id": state["meeting_id"], "native": state["native"]},
                    provider_ref=str(state["meeting_id"]))

    @reg.step
    def await_admission(ctx):
        m = rs.meeting_status(api_key, state["platform"], state["native"])
        st = (m.get("status") or m.get("meeting", {}).get("status") or "?")
        say(f"bot status: {st}")
        if st in ("active",):
            state["admitted_at"] = ctx.clock_now
            return Done({"status": st})
        if st in ("failed", "completed"):
            from flows import StepError
            raise StepError(f"bot ended early: {st}", retryable=False)
        return Wait(seconds=5)

    @reg.step
    def transcribe_window(ctx):
        elapsed = ctx.clock_now - state.get("admitted_at", ctx.clock_now)
        if elapsed < args.transcribe_s:
            return Wait(seconds=args.transcribe_s - elapsed)
        uid = f"witness-{state['meeting_id']}"
        n = rs.inject_fixture_transcript(state["meeting_id"], uid)
        say(f"fixture transcript injected: {n} segments (no whisper in this stack — the one double)")
        return Done({"segments": n})

    @reg.step
    def stop_bot(ctx):
        rs.stop_bot(api_key, state["platform"], state["native"])
        say("bot stopped — meeting completing")
        return Done({})

    @reg.step
    def await_completion(ctx):
        m = rs.meeting_status(api_key, state["platform"], state["native"])
        st = (m.get("status") or m.get("meeting", {}).get("status") or "?")
        if st != "completed":
            say(f"meeting status: {st}")
            return Wait(seconds=4)
        segs = m.get("segments") or m.get("transcript") or []
        state["transcript_text"] = "\n".join(
            f"{s.get('speaker','?')}: {s.get('text','')}" for s in segs) or "(see table)"
        say(f"meeting completed · transcript readable via API ({len(segs)} segments)")
        return Done({"segments": len(segs)})

    @reg.step
    def process_transcript(ctx):
        if "agent_started" not in state:
            say("REAL agent processing — worker container spawning (docker ps: vexa-worker-…)")
            rs.agent_process(subject, state["meeting_id"], state["native"], state["transcript_text"])
            state["agent_started"] = ctx.clock_now
            state["baseline_commits"] = len(rs.workspace_git(subject).get("commits", []))
            return Wait(seconds=10)
        git = rs.workspace_git(subject)
        commits = git.get("commits", [])
        # match the NOTE's commit by touched path/message — never by count (the witness caught the
        # seed commit masquerading as the note: PASS with the wrong sha in the email)
        note_commit = next((c for c in commits
                            if "meeting" in (c.get("msg", "").lower())
                            or any("kg/entities/meeting/" in f for f in (c.get("files") or []))), None)
        if note_commit:
            sha = note_commit.get("sha", "")[:9]
            say(f"agent committed the meeting note · {sha} · '{note_commit.get('msg','')[:60]}'")
            state["sha"] = sha
            note_path = next((f for f in (note_commit.get("files") or [])
                              if f.startswith("kg/entities/meeting/")), None)
            state["note_path"] = note_path
            return Done({"commit_sha": sha, "note_path": note_path or ""})
        if ctx.clock_now - state["agent_started"] > 600:
            from flows import StepError
            raise StepError("agent produced no commit in 10min", retryable=False)
        say("agent thinking…")
        return Wait(seconds=10)

    @reg.step
    def email_participants(ctx):
        # UI-LESS CONSTRAINT (founder 2026-08-23): the email IS the product — the note travels
        # IN the body, verbatim from the committed artifact. No links to any UI.
        sha = ctx.prior["process_transcript"]["commit_sha"]
        note_path = ctx.prior["process_transcript"].get("note_path") or state.get("note_path")
        note = (rs.workspace_file(subject, note_path) if note_path else None) \
            or "(note content unavailable — see reply)"
        body = f"{note}\n\n—\nRecorded by Vexa · commit {sha} · reply to this email to ask about the meeting."
        for r in [p for p in ctx.refs["participants"] if p.endswith("@bank.com")]:
            rs.send_mail(r, f"Minutes: {ctx.refs['title']}", body)
        say("summary emails → Mailpit — the NOTE ITSELF in the body (UI-less)")
        return Done({"sha": sha, "note_path": note_path})

    reg.flow(name="real_witness", version=1, on=INVITE,
             steps=[confirm_by_email, await_start, dispatch_bot, await_admission,
                    transcribe_window, stop_bot, await_completion, process_transcript,
                    email_participants])

    admit(db, reg, clock, source_event_id="real-inv-1", event_type=INVITE.name,
          subject_refs={**ev, "inviter": "anna@bank.com"})
    say("FACT admitted: invite.received (from the ICS fixture)")

    deadline = time.time() + 1200
    while time.time() < deadline:
        reclaim(db, clock); escalate(db, clock)
        if not tick(db, reg, clock):
            rows = db.execute("SELECT status FROM reaction")
            if all(r[0] in ("done", "failed", "cancelled") for r in rows):
                break
            time.sleep(1.0)

    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    st = status(db, rid)
    print("\n── receipts ──")
    for r in st["receipts"]:
        print(f"  {r['state']:<10} {r['step']:<20} {r.get('provider_ref') or ''}")
    print(f"\nVERDICT: {'PASS' if st['status'] == 'done' else 'FAIL — ' + str(st['reason'])}")
    print("HUMAN: Mailpit http://localhost:8025 · terminal http://localhost:3010 (meeting + note) ·"
          f" workspace commit {state.get('sha','?')}")
    return 0 if st["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
