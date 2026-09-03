"""Class D storm — conversation routing. A simulated mail world of users, threads, strangers,
bulk mail and abuse; invariants:
  R1 a threaded reply FROM THE THREAD'S OWN PARTICIPANT lands in EXACTLY its registered
     (uid, session) — the row decides the conversation, never a sender match
  R2 a threaded reply from ANYBODY ELSE never enters that conversation. `In-Reply-To` is an id,
     not an identity, and the message id of every mail we send is in that mail's headers — so a
     forged ref used to run an agent turn inside a stranger's session on a stranger's workspace
     with the forger's text (R-B12). The sender falls through to their own identity instead.
  R3 auto/bulk/no-reply NEVER produces a route unless it is a registered thread reply
  R4 a known user routes to their own session (unscaffolded → onboarding, scaffolded → main); an
     UNKNOWN human routes to onboarding only if they are inside the deployment's domain
     allow-list, and is QUARANTINED otherwise — no account, no agent turn, no model call
  R5 our own address never routes (echo-loop proof)
  R6 junk headers never throw"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlite_double import SqliteDB  # noqa: E402
from flows_integrations.mailbox import route  # noqa: E402
from flows_steps.emailx import register_thread  # noqa: E402

SELF = "info@vexa.ai"
# The deployment's inbound allow-list, passed explicitly so the storm never depends on the
# environment: `bank.com` is the org this instance serves. `evil.io` and `x.io` are not.
ALLOWED = {"bank.com"}


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
        out = route(db, SELF, frm, headers, None, known, scaff, allowed=ALLOWED)

        if frm == SELF:
            assert out is None, "R5: routed our own mail"
            continue
        ref = headers.get("In-Reply-To")
        mine = ref in threads and threads[ref][0] == users.get(frm)
        if mine:
            kind, p = out
            assert kind == "thread_reply" and (p["uid"], p["session"]) == threads[ref], "R1"
            continue
        bulk = headers.get("Precedence") in ("bulk", "list") or "Auto-Submitted" in headers \
            or "no-reply" in frm
        if bulk:
            assert out is None, f"R3: bulk/auto routed: {frm} {headers}"
            continue
        kind, p = out
        if ref in threads:
            assert kind != "thread_reply", "R2: a ref the sender is not a participant of"
        if frm in users:
            assert kind == "known_user_mail", "R4"
            assert p["session"] == ("main" if users[frm] == "1" else "onboarding"), "R4 session"
        elif frm.rsplit("@", 1)[-1] in ALLOWED:
            assert kind == "new_sender_mail" and p["session"] == "onboarding", "R4 colleague"
        else:
            assert kind == "quarantine", f"R4: a stranger was acted for: {frm}"


def test_a_forged_thread_ref_reaches_no_conversation_at_all():
    """R2, and it is the reverse of what this test used to assert.

    It used to say: *"a forged ref routes to the REGISTERED (uid, session), so the forger reaches
    a conversation but can never choose whose it is"* — and treated that as the safe property. It
    is not. Reaching a conversation IS the exploit: the message id of every mail we send is in
    that mail's headers, so anybody who has ever been copied on one — or who guesses — ran an
    agent turn inside somebody else's session, on somebody else's workspace, with their own text
    in the prompt, and got the answer mailed back to them (R-B12).

    The thread row still decides WHICH conversation a reply belongs to; what it no longer does is
    carry the sender into it."""
    db, users, threads, known, scaff = rig()
    kind, p = route(db, SELF, "attacker@evil.io", {"In-Reply-To": "<t0@vexa.ai>"}, None,
                    known, scaff, allowed=ALLOWED)
    assert kind == "quarantine"
    assert "t0@vexa.ai" in p["reason"] and p["kind"] == "thread_mismatch"


def test_a_colleague_who_forges_a_ref_gets_their_own_session_not_the_threads():
    """The allow-listed half of R2. Ben is a real user here and `<t0@vexa.ai>` is Anna's meeting
    thread: he is answered, in HIS session, and never inside hers."""
    db, users, threads, known, scaff = rig()
    kind, p = route(db, SELF, "ben@bank.com", {"In-Reply-To": "<t0@vexa.ai>"}, None,
                    known, scaff, allowed=ALLOWED)
    assert kind == "known_user_mail"
    assert (p["uid"], p["session"]) == ("2", "onboarding")


def test_an_unset_allow_list_means_our_own_domain_never_everyone():
    """The default is the trap this control is usually shipped with. Unset does not mean "admit
    the internet" — it means the mailbox's own domain, exactly as `VEXA_FLOWS_ATTENDEE_DOMAINS`
    unset means the organizer's (PRD §16.2)."""
    db, users, threads, known, scaff = rig()
    kind, _p = route(db, SELF, "stranger@x.io", {}, None, known, scaff)      # no `allowed=`
    assert kind == "quarantine"
    kind, p = route(db, SELF, "someone@vexa.ai", {}, None, known, scaff)     # SELF is info@vexa.ai
    assert kind == "new_sender_mail" and p["session"] == "onboarding"


def test_rsvp_echo_never_becomes_invite():
    ics = "BEGIN:VCALENDAR\nMETHOD:REPLY\nBEGIN:VEVENT\nUID:x\nDTSTART:20300101T000000Z\n" \
          "LOCATION:https://meet.google.com/aaa-bbbb-ccc\nORGANIZER:mailto:a@b.c\nEND:VEVENT\nEND:VCALENDAR"
    db, users, threads, known, scaff = rig()
    assert route(db, SELF, "calendar-notification@google.com", {}, ics, known, scaff,
                 allowed=ALLOWED) is None
