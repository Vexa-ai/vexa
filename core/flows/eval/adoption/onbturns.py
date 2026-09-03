"""How many human REPLIES does onboarding take before `.scaffolded` exists?

`require_workspace` gates every minutes mail on `.scaffolded`, and only the AGENT writes it,
after a threaded email conversation with the person. Seed strategy (iii) means onboarding ~10%
of an org, so the number of round trips per person — and the fact that each is blocked on a human
— is the whole cost of that strategy.

The person is simulated (Haiku, the coordinator persona, cooperative); the mail, the threading,
the agent and the marker are all real.
"""
import json
import os
import re
import smtplib
import sys
import time
import urllib.request
from email.message import EmailMessage

import judge
import personas

EMAIL = open("/tmp/onb_email").read().strip()
UID = sys.argv[1] if len(sys.argv) > 1 else "107"
MAILBOX = "vexa@sim.test"
MAX = int(os.environ.get("MAX_TURNS", "5"))


def mails():
    q = urllib.parse.quote(f'to:"{EMAIL}"')
    with urllib.request.urlopen(f"http://127.0.0.1:8025/api/v1/search?query={q}&limit=50") as r:
        return sorted(json.load(r).get("messages", []), key=lambda m: m["Created"])


def body_of(mid):
    with urllib.request.urlopen(f"http://127.0.0.1:8025/api/v1/message/{mid}") as r:
        d = json.load(r)
    return d.get("Text") or "", d.get("MessageID") or ""


def scaffolded():
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "http://127.0.0.1:18500/api/workspace/file?path=.scaffolded",
                headers={"X-User-Id": UID}), timeout=20) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def reply(text, in_reply_to, subject):
    m = EmailMessage()
    m["From"] = EMAIL
    m["To"] = MAILBOX
    m["Subject"] = "Re: " + subject
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(text)
    with smtplib.SMTP("127.0.0.1", 1025, timeout=20) as s:
        s.send_message(m)


import urllib.parse  # noqa: E402

PROMPT = """{brief}

You are a production coordinator. You are being onboarded to a meeting-notes tool by email and
you are willing — you want this to work, and you have two minutes.

THE EMAIL YOU JUST RECEIVED (verbatim):
---
{mail}
---

Write your reply. Answer whatever it asked, plainly and briefly, as yourself. If it asked for
nothing you can answer, say what you want it to do. Return ONLY a JSON object with keys:
{{"reply": "the text you send", "done": "bool — do you consider yourself set up now"}}"""

judge.ensure_cfg()
t0 = float(open("/tmp/onb_t0").read())
seen = set()
turns = 0
log = []
for step in range(MAX):
    # wait for a new inbound mail on the thread
    target = None
    for _ in range(40):
        for m in mails():
            if m["ID"] in seen:
                continue
            subj = m.get("Subject") or ""
            if subj.startswith(("Accepted:", "Prepare:", "Your Vexa sign-in")):
                seen.add(m["ID"])
                continue
            target = m
            break
        if target or scaffolded():
            break
        time.sleep(10)
    if scaffolded():
        print(f"  .scaffolded EXISTS after {turns} human repl{'y' if turns == 1 else 'ies'}")
        break
    if not target:
        print(f"  no new mail to reply to after {turns} replies — stopping")
        break
    seen.add(target["ID"])
    text, msgid = body_of(target["ID"])
    d = judge._json_out(judge._ask(PROMPT.format(
        brief=personas.PERSONAS["coordinator_under_pressure"][1], mail=text[:2500]))) or {}
    r = d.get("reply") or "Yes, please set it up."
    turns += 1
    print(f"  turn {turns}: agent said {len(text)} chars -> person replies: {r[:110]!r}", flush=True)
    log.append({"turn": turns, "agent": text[:1500], "person": r})
    reply(r, msgid, (target.get("Subject") or "Vexa").replace("Re: ", ""))
    time.sleep(20)

print(f"\nRESULT: {turns} human replies, scaffolded={scaffolded()}, "
      f"wall clock {round(time.time() - t0)}s from the invite")
json.dump({"email": EMAIL, "uid": UID, "turns": turns, "scaffolded": scaffolded(),
           "wall_s": round(time.time() - t0), "log": log},
          open(os.path.expanduser("~/sim-runs/r6/onboarding-turns.json"), "w"), indent=1)
