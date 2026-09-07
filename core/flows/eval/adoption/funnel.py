"""Revolution 4 — the SECOND-INVITE funnel, per persona, in three stages.

    OFFERED   was putting Vexa in a meeting of THEIR OWN ever put to them — in the mail, or in
              the chat the button opens?
    ASKED     did they take it (`asked_action: invite_mailbox`)?
    HAPPENED  did a real invite.received follow — a real ICS to the mailbox the poller watches,
              or a booking through bot_schedule?

Measured on real artifacts and a real agent, never asserted. Stage 1 is read out of the text the
product actually sent and said; stage 2 is the persona's own decision; stage 3 is a row in the
flows database or a booking receipt, not a claim in a transcript.

The WORDING of the offer is the founder's and is a placeholder here on purpose. What is being
measured is whether the mechanics carry a yes all the way to a meeting.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

import cohorts
import judge
import personas
import rig
import sample

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r4"))
AGENT_API = "http://127.0.0.1:18500"
COHORT = os.environ.get("SIM_COHORT", cohorts.PRODUCTION)

OFFER_WORDS = re.compile(
    r"your own meeting|meetings you run|be in your|invite .*vexa|forward the (calendar )?invite"
    r"|vexa@|add (me|vexa) to", re.I)


def agent_turn(uid, session, prompt, timeout=240):
    req = urllib.request.Request(f"{AGENT_API}/api/chat", method="POST",
                                 data=json.dumps({"prompt": prompt, "session": session}).encode())
    req.add_header("content-type", "application/json")
    req.add_header("X-User-Id", str(uid))
    reply = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode(errors="replace").strip()
                if line.startswith("data: "):
                    try:
                        ev = json.loads(line[6:])
                    except Exception:  # noqa: BLE001
                        continue
                    if ev.get("type") == "done":
                        reply = ev.get("reply") or reply
    except Exception as e:  # noqa: BLE001
        return f"(no answer: {type(e).__name__})"
    return reply


def flows_invites(prefix=""):
    """Stage 3, from the sim lane's own reaction table — a fact, not a transcript claim."""
    import simlane
    _st, body = simlane.reactions()
    rows = body.get("reactions", body) if isinstance(body, dict) else (body or [])
    return [r for r in (rows or [])
            if "invite_intake" in str(r.get("source_event_id", ""))
            and (not prefix or str(r.get("source_event_id", "")).startswith(prefix))]


def main():
    os.makedirs(RUN, exist_ok=True)
    cases = json.load(open(sys.argv[1]))
    before = len(flows_invites())
    out = []
    for c in cases:
        email, persona, mid, uid = c["email"], c["persona"], c["meeting_id"], c["uid"]
        role, dept = sample.ROLE_FOR.get(persona, ("artist", "Show A"))
        who = sample.FakePerson(persona, role, dept, c.get("name", "Sam Okafor"))
        mail = c.get("mail_text", "")

        # ── stage 1a: was it offered in the MAIL?
        offered_mail = bool(OFFER_WORDS.search(mail))

        # ── the chat the button opens
        opening = agent_turn(uid, f"meet-{mid}", c["ask_prompt"])
        offered_chat = bool(OFFER_WORDS.search(opening))

        d = judge.conv_open(who, opening)
        asked = d.get("asked_action") == "invite_mailbox"
        log = [{"who": "agent", "text": opening}, {"who": "person", "text": d.get("say", ""),
                                                   "judgment": d}]
        # one more turn — the offer usually lands after their first answer
        if d.get("say") and not d.get("abandoned"):
            second = agent_turn(uid, f"meet-{mid}", d["say"])
            offered_chat = offered_chat or bool(OFFER_WORDS.search(second))
            d2 = judge.conv_turn(who, d["say"], second)
            asked = asked or d2.get("asked_action") == "invite_mailbox"
            log += [{"who": "agent", "text": second},
                    {"who": "person", "text": d2.get("say", ""), "judgment": d2}]
            if asked and d2.get("say"):
                third = agent_turn(uid, f"meet-{mid}", d2["say"])
                log.append({"who": "agent", "text": third})

        acted_text = " ".join(x["text"] or "" for x in log if x["who"] == "agent")
        gave_route = bool(re.search(r"vexa@|bot_schedule|booked|scheduled", acted_text, re.I))
        out.append({"persona": persona, "email": email,
                    "offered_mail": offered_mail, "offered_chat": offered_chat,
                    "offered": offered_mail or offered_chat,
                    "asked": asked, "route_given": gave_route, "log": log})
        print(f"  {persona:28s} offered={out[-1]['offered']!s:5s} "
              f"(mail={offered_mail!s:5s} chat={offered_chat!s:5s}) asked={asked!s:5s} "
              f"route={gave_route}", flush=True)
        json.dump(out, open(f"{RUN}/funnel-{COHORT}.json", "w"), indent=1)

    after = len(flows_invites())
    n = len(out)
    print(f"\n=== SECOND-INVITE FUNNEL ({COHORT}, n={n}) ===")
    print(f"  OFFERED  {sum(o['offered'] for o in out)}/{n}"
          f"   (mail {sum(o['offered_mail'] for o in out)}, chat {sum(o['offered_chat'] for o in out)})")
    print(f"  ASKED    {sum(o['asked'] for o in out)}/{n}")
    print(f"  ROUTE    {sum(o['route_given'] for o in out)}/{n}   (a booking or the address)")
    print(f"  HAPPENED invite.received reactions in the sim lane: {before} -> {after}")


if __name__ == "__main__":
    main()
