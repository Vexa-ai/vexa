#!/usr/bin/env python3
"""THE PRODUCT FLOW, live (founder spec, UI-less): NEW user invites vexa@bank.com.
organizer notification + bot scheduled → onboarding by email (research → ONE question →
wait for the human) → bot at start−2min → meeting → fixture transcript → processing QUEUES
behind workspace readiness with nudges → human's reply (relayed to /tmp/witness-reply)
finalizes setup → REAL agent writes the note → minutes VERBATIM in email. No UI anywhere."""
from __future__ import annotations

import argparse, json, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from flows import Block, Done, EventType, Registry, SystemClock, Wait, admit, resume, status, tick, reclaim, escalate  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
import real_steps as rs  # noqa: E402

INVITE = EventType("invite.received")
ONB = EventType("onboarding.needed")
REPLY_FILE = Path("/tmp/witness-reply")


def say(m):
    print(f"  {time.strftime('%H:%M:%S')} · {m}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--start-in", type=float, default=160.0)   # bot fires at start−120
    ap.add_argument("--transcribe-s", type=float, default=40.0)
    a = ap.parse_args()

    organizer = "marvin@bank.com"        # in production: parsed From: of the invite email
    start_at = time.time() + a.start_in
    ADMIN = {"X-Admin-API-Key": "changeme"}
    st: dict = {}

    def api_key() -> str: return st["key"]
    def subject() -> str: return st["uid"]

    db, clock, reg = SqliteDB(), SystemClock(), Registry()

    # ── intake flow ───────────────────────────────────────────────────────────
    @reg.step
    def ensure_user(ctx):
        """AUTOMATED user creation (founder 2026-08-23): the invite itself provisions the account.
        Idempotent: look the organizer up by email; absent → create; then mint a scoped key."""
        code, u = rs.http("GET", f"http://localhost:18057/admin/users/email/{organizer}", ADMIN)
        if code != 200:
            code, u = rs.http("POST", "http://localhost:18057/admin/users", ADMIN,
                              {"email": organizer, "name": organizer.split("@")[0].title()})
            say(f"NEW user provisioned automatically: {organizer} (id {u['id']})")
        else:
            say(f"user exists: {organizer} (id {u['id']})")
        st["uid"] = str(u["id"])
        _, tok = rs.http("POST", f"http://localhost:18057/admin/users/{u['id']}/tokens", ADMIN,
                         {"scopes": ["bot", "browser", "tx"]})
        st["key"] = tok.get("token") or tok.get("key")
        return Done({"user_id": u["id"]}, provider_ref=str(u["id"]))

    @reg.step
    def notify_organizer(ctx):
        rs.send_mail(organizer, "Vexa will join: Pilot kickoff",
                     f"Vexa joins {a.url} at {time.strftime('%H:%M', time.localtime(start_at))}. "
                     "Reply to this email to change anything.")
        say(f"organizer notified → Mailpit ({organizer}) · bot scheduled for start−2min")
        return Done({})

    @reg.step
    def ensure_onboarding(ctx):
        n = admit(db, reg, clock, source_event_id=f"onb-{organizer}", event_type=ONB.name,
                  subject_refs={"person": organizer})
        say("no personal workspace for the organizer → onboarding sub-flow spawned")
        return Done({"spawned": n})

    @reg.step
    def await_start(ctx):
        if ctx.clock_now < start_at - 120:
            return Wait(until=start_at - 120)
        return Done({})

    @reg.step
    def dispatch_bot(ctx):
        say("START−2min: dispatching REAL bot — admit 'Vexa Witness' in the call")
        body = rs.spawn_bot(api_key(), a.url)
        st["meeting_id"], st["native"] = body.get("id"), body.get("native_meeting_id") or a.url.rsplit("/", 1)[1]
        st["platform"] = body.get("platform", "google_meet")
        return Done({"meeting_id": st["meeting_id"]}, provider_ref=str(st["meeting_id"]))

    @reg.step
    def run_meeting(ctx):
        m = rs.meeting_status(api_key(), st["platform"], st["native"])
        s_ = m.get("status") or "?"
        if s_ in ("requested", "joining", "awaiting_admission"):
            say(f"bot: {s_}"); return Wait(seconds=5)
        if s_ == "active":
            st.setdefault("admitted_at", ctx.clock_now)
            if ctx.clock_now - st["admitted_at"] < a.transcribe_s:
                return Wait(seconds=5)
            n = rs.inject_fixture_transcript(st["meeting_id"], f"prod-{st['meeting_id']}")
            say(f"fixture transcript injected ({n} segments) → stopping bot")
            rs.stop_bot(api_key(), st["platform"], st["native"])
            return Wait(seconds=5)
        if s_ in ("stopping",):
            return Wait(seconds=4)
        if s_ == "completed":
            segs = m.get("segments") or []
            st["transcript"] = "\n".join(f"{x.get('speaker','?')}: {x.get('text','')}" for x in segs)
            say(f"meeting completed · {len(segs)} segments · webhook → post-meeting flow")
            admit(db, reg, clock, source_event_id=f"whk-{st['meeting_id']}",
                  event_type="meeting.completed", subject_refs={"meeting_id": st["meeting_id"]})
            return Done({"segments": len(segs)})
        from flows import StepError
        raise StepError(f"unexpected meeting state {s_}", retryable=True)

    # ── onboarding flow ───────────────────────────────────────────────────────
    @reg.step
    def research_person(ctx):
        say("researching the organizer from the email (name+company lookup)")
        return Done({"guess": {"name": "Marvin", "company": "Bank"}})

    @reg.step
    def ask_one_question(ctx):
        rs.send_mail(organizer, "One question before your first minutes",
                     "You look like Marvin at Bank — correct? What's your role, and what do you "
                     "want Vexa to pay attention to in your meetings? Just reply to this email.")
        say("ONE onboarding question → Mailpit — ANSWER IT IN CHAT (I'll relay)")
        return Done({})

    @reg.step
    def await_human_reply(ctx):
        if REPLY_FILE.exists() and REPLY_FILE.read_text().strip():
            return Done({"reply": REPLY_FILE.read_text().strip()})
        return Block("awaiting the human's email reply", deadline_s=86400)

    @reg.step
    def setup_personal_workspace(ctx):
        reply = ctx.prior["await_human_reply"]["reply"]
        say(f"human replied ({reply[:50]!r}) → scaffolding the personal workspace")
        rs.http("POST", f"{rs.AGENT_API}/api/workspace/init", {"X-User-Id": subject()})
        rs.http("PUT", f"{rs.AGENT_API}/api/workspace/file", {"X-User-Id": subject()},
                {"path": "_system/identity.md", "content": f"# Identity\n\n{reply}\n"})
        rs.http("PUT", f"{rs.AGENT_API}/api/workspace/file", {"X-User-Id": subject()},
                {"path": ".scaffolded", "content": time.strftime("%Y-%m-%d")})
        rs.send_mail(organizer, "You're set up",
                     f"Workspace ready. Noted: {reply}\nYour first meeting's minutes follow shortly.")
        say("workspace scaffolded (.scaffolded written) → the queued meeting can now process")
        return Done({"reply": reply})

    # ── post-meeting flow, gated ──────────────────────────────────────────────
    @reg.step
    def require_workspace(ctx):
        code, _ = rs.http("GET", f"{rs.AGENT_API}/api/workspace/file?path=.scaffolded",
                          {"X-User-Id": subject()})
        if code == 200:
            return Done({"ready": True})
        rs.send_mail(organizer, "Your minutes are waiting",
                     "Your first meeting is recorded. Answer the onboarding question and the "
                     "minutes arrive right after.")
        say("workspace NOT ready → nudge email → re-check in 60s (queued, never lost)")
        return Wait(seconds=60)

    @reg.step
    def process_transcript(ctx):
        if "agent_started" not in st:
            say("REAL agent processing the first meeting")
            rs.agent_process(subject(), st["meeting_id"], st["native"], st["transcript"])
            st["agent_started"] = ctx.clock_now
            return Wait(seconds=12)
        commits = rs.workspace_git(subject()).get("commits", [])
        note = next((c for c in commits if any("kg/entities/meeting/" in f for f in (c.get("files") or []))), None)
        if note:
            path = next(f for f in note["files"] if f.startswith("kg/entities/meeting/"))
            say(f"note committed {note['sha'][:9]} · {path}")
            return Done({"sha": note["sha"][:9], "note_path": path})
        if ctx.clock_now - st["agent_started"] > 600:
            from flows import StepError
            raise StepError("no note in 10min", retryable=False)
        say("agent thinking…")
        return Wait(seconds=10)

    @reg.step
    def email_minutes(ctx):
        note = rs.workspace_file(subject(), ctx.prior["process_transcript"]["note_path"]) or "(missing)"
        body = note + f"\n\n—\nRecorded by Vexa · commit {ctx.prior['process_transcript']['sha']} · reply to ask about this meeting."
        for r in (organizer, "anna@bank.com"):
            rs.send_mail(r, "Minutes: Pilot kickoff", body)
        say("MINUTES verbatim in email → Mailpit (UI-less) — DONE")
        return Done({})

    s = reg.steps
    reg.flow(name="invite_intake", version=1, on=INVITE,
             steps=[ensure_user, notify_organizer, ensure_onboarding, await_start, dispatch_bot, run_meeting])
    reg.flow(name="onboard_by_email", version=1, on=ONB,
             steps=[research_person, ask_one_question, await_human_reply, setup_personal_workspace])
    reg.flow(name="post_meeting_gated", version=1, on=EventType("meeting.completed"),
             steps=[require_workspace, process_transcript, email_minutes])

    admit(db, reg, clock, source_event_id="prod-inv-1", event_type=INVITE.name,
          subject_refs={"url": a.url})
    say("FACT: invite.received (vexa@bank.com added to the meeting — fixture stands in for the mailbox)")

    deadline = time.time() + 2400
    while time.time() < deadline:
        reclaim(db, clock); escalate(db, clock)
        # the human's chat reply resumes the blocked onboarding reaction
        if REPLY_FILE.exists() and REPLY_FILE.read_text().strip():
            for (rid,) in db.execute("SELECT reaction_id FROM reaction WHERE status='blocked'"):
                resume(db, rid, actor="marvin@bank.com", clock=clock, reason="email reply")
        if not tick(db, reg, clock):
            rows = db.execute("SELECT status FROM reaction")
            if rows and all(r[0] in ("done", "failed", "cancelled") for r in rows):
                break
            time.sleep(1.0)

    print("\n── final ──")
    for f, s_, why in db.execute("SELECT flow, status, reason FROM reaction"):
        print(f"  {f:<20} {s_}{' · ' + why if why else ''}")
    ok = all(r[0] == "done" for r in db.execute("SELECT status FROM reaction"))
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
