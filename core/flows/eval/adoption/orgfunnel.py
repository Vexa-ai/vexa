"""Revolution 5B — ORGANIZER CONVERSION at a sample size that can carry a conclusion.

Revolution 4 measured ASKED at n=4 per arm and moved 0 -> 1 -> 0. That is noise, and it was
reported as noise. Haiku is cheap, so this runs n>=12 per persona per arm.

What is real and what is not, again: the MAIL and the CHAT OPENING are the artifacts the product
actually produced and are passed verbatim. The decision is sampled. Nothing here asserts a
propensity.

Three arms, and the third is the H2 test:
  mailed             the offer as it now stands — in the mail and in the chat
  mailed_presence    the same, for somebody who has ALSO seen the bot in their dailies all week
  presence_only      never mailed; the ONLY exposure is the bot's name in the participant list
The third answers whether presence converts on its own or only multiplies a touch — the claim H2
makes is that it exposes but rarely converts, and it has never been measured.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

import judge
import personas
import rig
import sample

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r5"))
N = int(os.environ.get("ORG_N", "12"))
PERSONAS = ["coordinator_under_pressure", "production_manager", "supervisor", "artist"]

PRESENCE = ("Every day this week, in your own Show B dailies, the participant list has had an "
            "extra name in it: \"Vexa Minutes\". Somebody added it. You have seen it sitting "
            "there in every session and nobody has complained about it.")

SCHEMA = {
    "understood_offer": "bool — did you realise you were being offered to have it in YOUR meetings",
    "will_add": "bool — will you actually put it on a meeting you run, starting tomorrow",
    "blocker": "one of: none | permission | privacy | effort | trust_quality | not_my_call "
               "| dont_need | unclear_how",
    "why": "str — one sentence, first person, why you will or will not",
}

PROMPT = """You are simulating one person's decision, honestly and in character.

WHO YOU ARE
{brief}

You are a {role} on Show B at a visual-effects studio. You run or sit in that show's dailies
every day.

{exposure}

DECIDE, concretely: will you add Vexa to the Show B dailies YOU are in, starting tomorrow?
Not "is it interesting" — will you do the thing. If something stops you, name which.

Return ONLY a JSON object, no prose and no code fence, with exactly these keys:
{schema}
"""


def newest(addr, needle):
    for m in rig.messages_for(addr):
        if needle in (m.get("Subject") or ""):
            t = rig.full_touch(m)
            if t["text"].strip():
                return t["text"]
    return ""


def arm(label: str, mail: str, opening: str, presence: bool):
    ex = []
    if mail:
        ex.append("THIS ARRIVED IN YOUR INBOX (verbatim, the real message):\n---\n"
                  + mail[:2600] + "\n---")
    if opening:
        ex.append("YOU OPENED IT AND THE CHAT SAID (verbatim):\n---\n" + opening[:1800] + "\n---")
    if presence:
        ex.append(PRESENCE)
    if not ex:
        ex.append("You have had no contact from this tool at all.")
    exposure = "\n\n".join(ex)

    jobs, keys = [], []
    for persona in PERSONAS:
        role, _dept = sample.ROLE_FOR.get(persona, ("artist", "Show B"))
        brief = personas.PERSONAS[persona][1]
        p = PROMPT.format(brief=brief, role=role.replace("_", " "), exposure=exposure,
                          schema=json.dumps(SCHEMA, indent=1))
        for _ in range(N):
            jobs.append(p)
            keys.append(persona)

    judge.ensure_cfg()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=12) as ex_:
        raw = list(ex_.map(lambda x: judge._json_out(judge._ask(x)), jobs))

    agg = defaultdict(lambda: {"n": 0, "und": 0, "yes": 0})
    blockers, whys, errs = defaultdict(int), [], 0
    for persona, a in zip(keys, raw):
        if not a:
            errs += 1
            continue
        g = agg[persona]
        g["n"] += 1
        g["und"] += bool(a.get("understood_offer"))
        g["yes"] += bool(a.get("will_add"))
        if not a.get("will_add"):
            blockers[str(a.get("blocker", "unclear"))] += 1
        if a.get("why"):
            whys.append({"persona": persona, "will_add": bool(a.get("will_add")),
                         "blocker": a.get("blocker"), "why": a["why"]})
    return {"arm": label, "n_per_persona": N, "errors": errs,
            "by_persona": {k: {"n": v["n"],
                               "understood": round(v["und"] / v["n"], 3) if v["n"] else 0,
                               "will_add": round(v["yes"] / v["n"], 3) if v["n"] else 0}
                           for k, v in agg.items()},
            "overall_n": sum(v["n"] for v in agg.values()),
            "overall_will_add": round(sum(v["yes"] for v in agg.values())
                                      / max(1, sum(v["n"] for v in agg.values())), 3),
            "blockers": dict(sorted(blockers.items(), key=lambda x: -x[1])),
            "whys": whys}


def main():
    os.makedirs(RUN, exist_ok=True)
    mail = newest(os.environ.get("ORG_MAILBOX", "sim-inv2-eng1@rehearsal.test"),
                  "what it means for you")
    opening = ""
    fp = os.path.expanduser("~/sim-runs/r4f/funnel-production.json")
    if os.path.exists(fp):
        for c in json.load(open(fp)):
            for l in c["log"]:
                if l["who"] == "agent" and (l.get("text") or "").strip():
                    opening = l["text"]
                    break
            if opening:
                break
    print(f"mail {len(mail)} chars · chat opening {len(opening)} chars · "
          f"{N} per persona per arm\n")

    out = []
    for label, m, o, pr in (("mailed", mail, opening, False),
                            ("mailed_presence", mail, opening, True),
                            ("presence_only", "", "", True)):
        r = arm(label, m, o, pr)
        out.append(r)
        print(f"{label:<18} n={r['overall_n']:<4} will_add={r['overall_will_add']*100:5.1f}%  "
              f"errors={r['errors']}")
        for p, v in sorted(r["by_persona"].items()):
            print(f"    {p:<28} n={v['n']:<3} understood={v['understood']*100:5.1f}% "
                  f"will_add={v['will_add']*100:5.1f}%")
        print(f"    blockers: {r['blockers']}")
        json.dump(out, open(f"{RUN}/organizer-funnel.json", "w"), indent=1)
        print()


if __name__ == "__main__":
    sys.exit(main())
