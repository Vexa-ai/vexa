"""Class D storm — conversation routing. A simulated mail world of users, threads, strangers,
bulk mail and abuse; invariants:
  R1 a threaded reply lands in EXACTLY its registered (uid, session) — never sender-matched
  R2 a reply never crosses users: thread registered to A routes to A even if B forges In-Reply-To? —
     (routing is by the thread row alone; the payload uid IS the thread's uid — asserted)
  R3 auto/bulk/no-reply NEVER produces a route unless it is a registered thread reply
  R4 unknown humans route to onboarding; known unscaffolded → onboarding; scaffolded → main
  R5 our own address never routes (echo-loop proof)
  R6 junk headers never throw"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import SqliteDB  # noqa: E402
from flows_integrations.mailbox import route  # noqa: E402
from flows_steps.emailx import register_thread  # noqa: E402

SELF = "info@vexa.ai"


def rig():
    db = SqliteDB()
    users = {"anna@bank.com": "1", "ben@bank.com": "2"}
    scaffolded = {"1"}                          # anna done, ben mid-onboarding
    threads = {}
    for i, (uid, session) in enumerate([("1", "meet-9"), ("2", "onboarding"), ("1", "main")]):
        mid = f"<t{i}@vexa.ai>"
        register_thread(db, mid, uid, session)
        threads[mid] = (uid, session)
    known = lambda e: users.get(e)
    scaff = lambda u: u in scaffolded
    return db, users, threads, known, scaff


def test_invariants_hold_under_storm():
    db, users, threads, known, scaff = rig()
    rnd = random.Random(4)
    for i in range(2000):
        case = rnd.random()
        frm = rnd.choice(list(users) + ["stranger@x.io", "no-reply@spam.io", SELF])
        headers = {}
        if case < 0.35 and threads:                       # threaded reply (any sender!)
            mid = rnd.choice(list(threads))
            headers["In-Reply-To"] = mid
        if rnd.random() < 0.25:
            headers["Precedence"] = rnd.choice(["bulk", "list", ""])
        if rnd.random() < 0.15:
            headers["Auto-Submitted"] = "auto-replied"
        if rnd.random() < 0.1:                            # junk header noise (R6)
            headers["References"] = "".join(chr(rnd.randrange(33, 127)) for _ in range(30))
        out = route(db, SELF, frm, headers, None, known, scaff)

        if frm == SELF:
            assert out is None, "R5: routed our own mail"
            continue
        ref = headers.get("In-Reply-To")
        if ref and ref in threads:
            kind, p = out
            assert kind == "thread_reply" and (p["uid"], p["session"]) == threads[ref], "R1"
            continue
        bulk = headers.get("Precedence") in ("bulk", "list") or "Auto-Submitted" in headers \
            or "no-reply" in frm
        if bulk:
            assert out is None, f"R3: bulk/auto routed: {frm} {headers}"
            continue
        kind, p = out
        if frm in users:
            assert kind == "known_user_mail", "R4"
            assert p["session"] == ("main" if users[frm] == "1" else "onboarding"), "R4 session"
        else:
            assert kind == "new_sender_mail" and p["session"] == "onboarding", "R4 stranger"


def test_forged_thread_reply_still_lands_in_registered_conversation_only():
    """R2: In-Reply-To is an ID lookup, not authority — a forged ref routes to the REGISTERED
    (uid, session), so the forger reaches a conversation but can never choose whose it is beyond
    what the thread row says. (Content authz is the agent/domain's re-check, per the PRD.)"""
    db, users, threads, known, scaff = rig()
    out = route(db, SELF, "attacker@evil.io", {"In-Reply-To": "<t0@vexa.ai>"}, None, known, scaff)
    kind, p = out
    assert (p["uid"], p["session"]) == ("1", "meet-9")     # the row's binding, never the sender's claim


def test_rsvp_echo_never_becomes_invite():
    ics = "BEGIN:VCALENDAR\nMETHOD:REPLY\nBEGIN:VEVENT\nUID:x\nDTSTART:20300101T000000Z\n" \
          "LOCATION:https://meet.google.com/aaa-bbbb-ccc\nORGANIZER:mailto:a@b.c\nEND:VEVENT\nEND:VCALENDAR"
    db, users, threads, known, scaff = rig()
    assert route(db, SELF, "calendar-notification@google.com", {}, ics, known, scaff) is None
