"""Revolution 6C — does the bot's NAME in the participant list move presence-only conversion?

presence_only converts coordinators well and everyone else poorly, and its dominant blocker is
`trust_quality` — "I've only seen Vexa in the participant list for a week; I haven't actually
verified it's writing notes correctly." Familiarity without evidence.

The cheapest evidence the product can carry is the name itself (PRD 16.2 item 4: the bot display
name carries the address). Three arms, presence ONLY, nobody mailed:

  plain      "Vexa Minutes"                              — what ships today
  hinted     the name says WHERE the notes are           — evidence is one look away
  hinted+day1 the same, plus the first minutes mail having reached the whole dailies on day 1

Wording is a placeholder as everywhere else; what is measured is whether carrying the pointer at
all moves the decision.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import judge
import personas
import sample

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r6"))
N = int(os.environ.get("ORG_N", "12"))
PERSONAS = ["coordinator_under_pressure", "production_manager", "supervisor", "artist"]

ARMS = {
    "plain": ("Every day this week, in your own Show B dailies, the participant list has had an "
              "extra name in it: \"Vexa Minutes\". Somebody added it. You have seen it sitting "
              "there in every session and nobody has complained about it."),
    "hinted": ("Every day this week, in your own Show B dailies, the participant list has had an "
               "extra name in it: \"Vexa Minutes — notes at app.dev.vexa.ai\". Somebody added "
               "it. You have seen it sitting there in every session and nobody has complained."),
    "hinted_day1": ("Every day this week, in your own Show B dailies, the participant list has "
                    "had an extra name in it: \"Vexa Minutes — notes at app.dev.vexa.ai\". On "
                    "the first day, everyone who was in that dailies — you included — got an "
                    "email with the notes of that session, and they were accurate. You have "
                    "seen the name in every session since and nobody has complained."),
}

SCHEMA = {
    "will_add": "bool — will you put it on a meeting you run, starting tomorrow",
    "checked": "bool — have you actually looked at what it produces",
    "blocker": "one of: none | permission | privacy | effort | trust_quality | not_my_call "
               "| dont_need | unclear_how",
    "why": "str — one sentence, first person",
}

PROMPT = """You are simulating one person's decision, honestly and in character.

WHO YOU ARE
{brief}

You are a {role} on Show B at a visual-effects studio, in that show's dailies every day.

{exposure}

You have had NO email from this tool and nobody has explained it to you.

DECIDE, concretely: will you add it to the Show B dailies YOU are in, starting tomorrow? Not "is
it interesting" — will you do the thing. If something stops you, name which.

Return ONLY a JSON object, no prose and no code fence, with exactly these keys:
{schema}
"""


def run_arm(label, exposure):
    jobs, keys = [], []
    for persona in PERSONAS:
        role, _ = sample.ROLE_FOR.get(persona, ("artist", "Show B"))
        p = PROMPT.format(brief=personas.PERSONAS[persona][1], role=role.replace("_", " "),
                          exposure=exposure, schema=json.dumps(SCHEMA, indent=1))
        for _ in range(N):
            jobs.append(p)
            keys.append(persona)
    judge.ensure_cfg()
    with ThreadPoolExecutor(max_workers=12) as ex:
        raw = list(ex.map(lambda x: judge._json_out(judge._ask(x)), jobs))
    agg = defaultdict(lambda: {"n": 0, "yes": 0, "chk": 0})
    blockers, whys, errs = defaultdict(int), [], 0
    for persona, a in zip(keys, raw):
        if not a:
            errs += 1
            continue
        g = agg[persona]
        g["n"] += 1
        g["yes"] += bool(a.get("will_add"))
        g["chk"] += bool(a.get("checked"))
        if not a.get("will_add"):
            blockers[str(a.get("blocker", "unclear"))] += 1
        if a.get("why"):
            whys.append({"persona": persona, "will_add": bool(a.get("will_add")),
                         "blocker": a.get("blocker"), "why": a["why"]})
    tot = sum(v["n"] for v in agg.values())
    return {"arm": label, "n": tot, "errors": errs,
            "will_add": round(sum(v["yes"] for v in agg.values()) / max(1, tot), 3),
            "checked": round(sum(v["chk"] for v in agg.values()) / max(1, tot), 3),
            "by_persona": {k: {"n": v["n"],
                               "will_add": round(v["yes"] / v["n"], 3) if v["n"] else 0}
                           for k, v in agg.items()},
            "blockers": dict(sorted(blockers.items(), key=lambda x: -x[1])), "whys": whys}


def main():
    os.makedirs(RUN, exist_ok=True)
    out = []
    for label, ex in ARMS.items():
        r = run_arm(label, ex)
        out.append(r)
        print(f"{label:<14} n={r['n']:<4} will_add={r['will_add']*100:5.1f}%  "
              f"looked_at_output={r['checked']*100:5.1f}%  errors={r['errors']}")
        for p, v in sorted(r["by_persona"].items()):
            print(f"    {p:<28} n={v['n']:<3} will_add={v['will_add']*100:5.1f}%")
        print(f"    blockers: {r['blockers']}")
        json.dump(out, open(f"{RUN}/trust-lever.json", "w"), indent=1)
        print()


if __name__ == "__main__":
    sys.exit(main())
