#!/usr/bin/env python3
"""FULLY REAL, UI-less, end to end — the mailbox is the front door:

  you invite info@vexa.ai to a Meet from your calendar
    → its REAL inbox receives the ICS → invite.received
    → REAL mail to the organizer (you): notification · onboarding question
    → REAL bot at start−2min (admit it) · fixture transcript stands in for whisper
    → minutes QUEUE behind onboarding; nudges to your REAL inbox
    → you REPLY from your mail client → the poller sees it → workspace scaffolds
    → REAL agent writes the note → MINUTES verbATIM in your inbox.

Safety: this run emails ONLY the organizer (you) and itself — never other participants."""
from __future__ import annotations

import re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from flows import Block, Done, EventType, Registry, SqliteDB, SystemClock, Wait, admit, resume, tick, reclaim, escalate  # noqa: E402
import real_steps as rs  # noqa: E402
import mail_real as mr  # noqa: E402

INVITE, ONB = EventType("invite.received"), EventType("onboarding.needed")
ADMIN = {"X-Admin-API-Key": "changeme"}

EMAIL_ONBOARDING_KICKOFF = """[email-onboarding] You are onboarding this person OVER EMAIL — every reply you
write is sent verbatim as a plain-text email, and their email replies come back as your next turn.
Read flows/personal.md and CLAUDE.md and run the discovery loop, adapted to email: research what
you can yourself; ask ONE question per email, short and warm, never a form; never repeat a question
they already answered. Record the name in _system/identity.md, build the self: true person entity,
keep README.md the dashboard. When your acceptance test passes (name recorded · self entity ·
identity · dashboard), write the file `.scaffolded` (content: today's date) — that releases their
first meeting's minutes — and say so in your final email. Plain text only: no markdown headers, no
links. Their address: """



def say(m): print(f"  {time.strftime('%H:%M:%S')} · {m}", flush=True)


def parse_ics(ics: str) -> dict:
    # parse the VEVENT block ONLY — a VTIMEZONE's DST-rule lines are literally DTSTART:1970…,
    # and matching them dispatched a bot 56 years "late" (the 16:07 lobby incident)
    ve = ics.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    org = re.search(r"ORGANIZER[^:]*:(?:mailto:)?([^\s]+)", ve, re.I)
    url = re.search(r"https://meet\.google\.com/[a-z-]+", ics)
    dt = re.search(r"DTSTART(?:;TZID=([^:;]+))?[^:]*:(\d{8}T\d{6})(Z?)", ve)
    uid = re.search(r"^UID:(.+)$", ve, re.M)
    start = time.time() + 150
    if dt:
        import calendar as cal
        from zoneinfo import ZoneInfo
        from datetime import datetime
        t = time.strptime(dt.group(2), "%Y%m%dT%H%M%S")
        if dt.group(3) == "Z":
            start = cal.timegm(t)
        elif dt.group(1):                                  # honor TZID
            start = datetime(*t[:6], tzinfo=ZoneInfo(dt.group(1))).timestamp()
        else:
            start = time.mktime(t)
    summ = re.search(r"^SUMMARY:(.+)$", ve, re.M)
    return {"organizer": (org.group(1) if org else "").strip().lower(),
            "url": url.group(0) if url else None,
            "start": start, "ics_uid": (uid.group(1).strip() if uid else "?"),
            "title": (summ.group(1).strip() if summ else "Meeting")}


def main() -> int:
    db, clock, reg = SqliteDB(), SystemClock(), Registry()
    st: dict = {}

    def api_key(): return st["key"]
    def subject(): return st["uid"]
    def organizer(): return st["organizer"]

    # ── steps (REAL mail; recipients: organizer only) ─────────────────────────
    @reg.step
    def ensure_user(ctx):
        o = ctx.refs["organizer"]
        code, u = rs.http("GET", f"http://localhost:18057/admin/users/email/{o}", ADMIN)
        if code != 200:
            code, u = rs.http("POST", "http://localhost:18057/admin/users", ADMIN,
                              {"email": o, "name": o.split("@")[0].title()})
            say(f"user provisioned automatically: {o} (id {u['id']})")
        else:
            say(f"user exists: {o} (id {u['id']})")
        st["uid"] = str(u["id"])
        _, tok = rs.http("POST", f"http://localhost:18057/admin/users/{u['id']}/tokens", ADMIN,
                         {"scopes": ["bot", "browser", "tx"]})
        st["key"] = tok.get("token") or tok.get("key")
        st["organizer"] = o
        return Done({"user_id": u["id"]})

    @reg.step
    def notify_organizer(ctx):
        mid = mr.send(organizer(), f"Vexa will join: {ctx.refs['title']}",
                      f"Vexa joins {ctx.refs['url']} at "
                      f"{time.strftime('%H:%M', time.localtime(ctx.refs['start']))}. "
                      "Reply to this email to change anything.")
        say(f"REAL mail → {organizer()}: joining confirmation")
        return Done({"message_id": mid}, provider_ref=mid)

    @reg.step
    def rsvp_accept(ctx):
        """Confirm attendance IN THE CALENDAR — iMIP REPLY over SMTP; the organizer's guest
        list flips Vexa to 'Yes'."""
        mid = mr.send_rsvp_accept(st.get("raw_ics", ""), organizer())
        say("RSVP ACCEPTED sent — check the guest list: Vexa shows 'Yes'")
        return Done({"message_id": mid}, provider_ref=mid)

    @reg.step
    def ensure_onboarding(ctx):
        code, _ = rs.http("GET", f"{rs.AGENT_API}/api/workspace/file?path=.scaffolded",
                          {"X-User-Id": subject()})
        if code == 200:
            say("workspace already set up — no onboarding needed")
            return Done({"already": True})
        admit(db, reg, clock, source_event_id=f"onb-{organizer()}", event_type=ONB.name,
              subject_refs={"person": organizer(), "title": ctx.refs["title"]})
        return Done({"spawned": 1})

    @reg.step
    def await_start(ctx):
        if ctx.clock_now < ctx.refs["start"] - 120:
            say(f"waiting: bot fires at start−2min ({int(ctx.refs['start'] - 120 - ctx.clock_now)}s)")
            return Wait(until=ctx.refs["start"] - 120)
        return Done({})

    @reg.step
    def dispatch_bot(ctx):
        say("START−2min → REAL bot dispatching — admit 'Vexa Witness'")
        body = rs.spawn_bot(api_key(), ctx.refs["url"])
        st["meeting_id"] = body.get("id")
        st["native"] = body.get("native_meeting_id") or ctx.refs["url"].rsplit("/", 1)[1]
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
            if ctx.clock_now - st["admitted_at"] < 40:
                return Wait(seconds=5)
            n = rs.inject_fixture_transcript(st["meeting_id"], f"live-{st['meeting_id']}")
            say(f"fixture transcript injected ({n} segments) → stopping bot")
            rs.stop_bot(api_key(), st["platform"], st["native"])
            return Wait(seconds=5)
        if s_ == "stopping":
            return Wait(seconds=4)
        if s_ == "completed":
            segs = m.get("segments") or []
            st["transcript"] = "\n".join(f"{x.get('speaker','?')}: {x.get('text','')}" for x in segs)
            say(f"meeting completed · {len(segs)} segments → post-meeting flow")
            admit(db, reg, clock, source_event_id=f"whk-{st['meeting_id']}",
                  event_type="meeting.completed",
                  subject_refs={"meeting_id": st["meeting_id"], "title": ctx.refs["title"]})
            return Done({"segments": len(segs)})
        from flows import StepError
        raise StepError(f"meeting state {s_}", retryable=True)

    def _history_len() -> int:
        code, hist = rs.http("GET", f"{rs.AGENT_API}/api/sessions/onboarding/history",
                             {"X-User-Id": subject()})
        return len(hist) if isinstance(hist, list) else 0

    def _latest_agent_reply():
        code, hist = rs.http("GET", f"{rs.AGENT_API}/api/sessions/onboarding/history",
                             {"X-User-Id": subject()})
        if isinstance(hist, list) and hist and hist[-1].get("role") == "agent" and hist[-1].get("text"):
            return len(hist), hist[-1]["text"].strip()
        return (len(hist) if isinstance(hist, list) else 0), None

    def _dispatch_agent(text: str) -> None:
        rs.http("POST", f"{rs.AGENT_API}/api/chat", {"X-User-Id": subject()},
                {"prompt": text, "session": "onboarding"}, timeout=8)

    @reg.step
    def start_onboarding_chat(ctx):
        """NEVER blocks (the 16:14 freeze): dispatch the agent turn and return — the engine's
        Wait does the waiting while the runner keeps polling mail and driving other reactions."""
        rs.http("POST", f"{rs.AGENT_API}/api/workspace/init", {"X-User-Id": subject()})
        kick = EMAIL_ONBOARDING_KICKOFF + ctx.refs["person"]
        if st.get("inbound"):
            kick += "\n\nThey have ALREADY written this — start from it, do not re-ask: " + st.pop("inbound")
        st["hist_seen"] = _history_len()
        _dispatch_agent(kick)
        say("agent onboarding session dispatched (non-blocking)")
        return Done({"dispatched": True})

    @reg.step
    def converse_until_scaffolded(ctx):
        code, _ = rs.http("GET", f"{rs.AGENT_API}/api/workspace/file?path=.scaffolded",
                          {"X-User-Id": subject()})
        if code == 200:
            say("agent accepted: .scaffolded written — onboarding complete")
            return Done({"ready": True})
        n, reply = _latest_agent_reply()
        if reply and n > st.get("hist_seen", 0) and not st.get("turn_pending_send") == n:
            st["hist_seen"] = n
            mid = mr.send(organizer(), ("Re: " if st.get("onb_thread") else "") + "Getting you set up",
                          reply, in_reply_to=st.get("onb_thread"))
            st.setdefault("onb_thread", mid)
            say("agent email → organizer")
        if st.get("inbound"):
            text = st.pop("inbound")
            say(f"inbound email → agent turn: {text[:50]!r}")
            _dispatch_agent(text)
        return Wait(seconds=8)

    @reg.step
    def require_workspace(ctx):
        code, _ = rs.http("GET", f"{rs.AGENT_API}/api/workspace/file?path=.scaffolded",
                          {"X-User-Id": subject()})
        if code == 200:
            return Done({"ready": True})
        if ctx.reaction.attempt % 5 == 1:      # nudge every ~5th check, not every minute
            mr.send(organizer(), "Your minutes are waiting",
                    "Your meeting is recorded. Answer the onboarding question (one email up) "
                    "and the minutes arrive right after.")
            say("nudge → organizer (queued, never lost)")
        return Wait(seconds=60)

    @reg.step
    def process_transcript(ctx):
        if "agent_started" not in st:
            say("REAL agent processing the meeting")
            rs.agent_process(subject(), st["meeting_id"], st["native"], st["transcript"])
            st["agent_started"] = ctx.clock_now
            st["baseline"] = [c["sha"] for c in rs.workspace_git(subject()).get("commits", [])]
            return Wait(seconds=12)
        commits = rs.workspace_git(subject()).get("commits", [])
        note = next((c for c in commits if c["sha"] not in st["baseline"]
                     and any("kg/entities/meeting/" in f for f in (c.get("files") or []))), None)
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
        mr.send(organizer(), f"Minutes: {ctx.refs.get('title','Meeting')}",
                note + f"\n\n—\nRecorded by Vexa · commit {ctx.prior['process_transcript']['sha']}"
                       " · reply to ask about this meeting.")
        say(f"MINUTES verbatim → {organizer()} — DONE")
        return Done({})

    reg.flow(name="invite_intake", version=1, on=INVITE,
             steps=[ensure_user, notify_organizer, rsvp_accept, ensure_onboarding, await_start, dispatch_bot, run_meeting])
    reg.flow(name="onboard_by_email", version=1, on=ONB,
             steps=[start_onboarding_chat, converse_until_scaffolded])
    reg.flow(name="post_meeting_gated", version=1, on=EventType("meeting.completed"),
             steps=[require_workspace, process_transcript, email_minutes])

    # ── the INTEGRATION: the real inbox → facts ───────────────────────────────
    cursor = int(sys.argv[1]) if len(sys.argv) > 1 else 579
    say(f"watching info@vexa.ai inbox (uid > {cursor}) — send the calendar invite now")
    last_poll = 0.0
    deadline = time.time() + 3600
    while time.time() < deadline:
        if time.time() - last_poll > 15:
            last_poll = time.time()
            try:
                for m in mr.poll(cursor):
                    cursor = max(cursor, m.uid)
                    if m.ics and "BEGIN:VEVENT" in m.ics:
                        ev = parse_ics(m.ics)
                        if not ev["url"]:
                            say(f"invite without a Meet link ignored ({ev['title']})"); continue
                        say(f"REAL ICS: '{ev['title']}' from {ev['organizer']} at "
                            f"{time.strftime('%H:%M', time.localtime(ev['start']))} → invite.received")
                        st["raw_ics"] = m.ics
                        admit(db, reg, clock, source_event_id=f"ics-{ev['ics_uid']}",
                              event_type=INVITE.name, subject_refs=ev)
                    elif st.get("organizer") and m.from_addr.lower() == st["organizer"]:
                        body = "\n".join(l for l in m.body.strip().splitlines()
                                          if not l.strip().startswith(">"))[:1500]
                        say(f"REAL reply from {m.from_addr}: {body[:60]!r}")
                        st["inbound"] = body
            except Exception as e:  # noqa: BLE001
                say(f"mailbox poll hiccup: {type(e).__name__} (retrying)")
        reclaim(db, clock); escalate(db, clock)
        if not tick(db, reg, clock):
            rows = db.execute("SELECT status FROM reaction")
            if rows and all(r[0] in ("done", "failed", "cancelled") for r in rows):
                break
            time.sleep(1.0)

    print("\n── final ──")
    for f, s_, why in db.execute("SELECT flow, status, reason FROM reaction"):
        print(f"  {f:<20} {s_}{' · ' + why if why else ''}")
    ok = bool(db.execute("SELECT 1 FROM reaction")) and \
        all(r[0] == "done" for r in db.execute("SELECT status FROM reaction"))
    print("VERDICT:", "PASS" if ok else "FAIL/incomplete")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
