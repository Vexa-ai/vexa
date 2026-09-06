"""Before/after on the MAIL TEXT alone, same personas, same histories, same fixture.

The only thing that differs between the two arms is the two provenance lines at the top of the
body — the meeting you were in and who recorded it, then where the words live and how to stop.
Everything else (the note, the link, the subject) is byte-identical, because both texts came out
of the same flows engine on the same fixture.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import judge
import personas
import rig
import sample

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r1"))
REPS = int(os.environ.get("AB_REPS", "3"))


def newest(addr: str, needle: str):
    for m in rig.messages_for(addr):
        if needle in (m.get("Subject") or ""):
            t = rig.full_touch(m)
            if t["text"].strip():
                return t
    return None


def arm(label: str, touch: dict) -> dict:
    jobs, keys = [], []
    for persona in personas.PERSONAS:
        role, dept = sample.ROLE_FOR.get(persona, ("artist", "Show A"))
        who = sample.FakePerson(persona, role, dept, "Sam Okafor")
        for hname, hist in sample.HISTORY_STATES.items():
            for _ in range(REPS):
                jobs.append((who, touch, hist, 10, 1 if hname == "fresh" else 4))
                keys.append((persona, hname))
    ans = judge.decide_many(jobs, workers=10)
    n = op = act = opt = 0
    whys = []
    for (persona, hname), a in zip(keys, ans):
        if a.get("_error"):
            continue
        n += 1
        op += bool(a.get("opened"))
        act += bool(a.get("active_action"))
        opt += bool(a.get("opted_out"))
        if a.get("why"):
            whys.append({"persona": persona, "history": hname,
                         "opened": a.get("opened"), "acted": a.get("active_action"),
                         "friction": a.get("friction", ""), "why": a["why"]})
    return {"label": label, "n": n, "chars": len(touch["text"]),
            "open": round(op / n, 4) if n else 0,
            "act_overall": round(act / n, 4) if n else 0,
            "opted_out": round(opt / n, 4) if n else 0,
            "whys": whys}


def main():
    before = json.load(open(f"{RUN}/touches.json")).get("attendee_shared")
    after = newest(sys.argv[1] if len(sys.argv) > 1 else "sim-prov-eng1@rehearsal.test",
                   "what it means for you")
    if not after:
        raise SystemExit("no post-change attendee mail found yet")
    if "You were in" not in after["text"]:
        raise SystemExit("the harvested mail carries no provenance line — is the sim lane on the new code?")
    print(f"BEFORE {len(before['text'])} chars · AFTER {len(after['text'])} chars\n")
    out = [arm("before (no provenance)", before), arm("after (provenance)", after)]
    for r in out:
        print(f"{r['label']:26s} n={r['n']:3d} chars={r['chars']:5d} "
              f"open={r['open']*100:5.1f}%  acted={r['act_overall']*100:5.1f}%"
              f"  opted-out={r['opted_out']*100:5.1f}%")
    json.dump({"arms": out, "after_text": after["text"][:1200]},
              open(f"{RUN}/ab-mailtext.json", "w"), indent=1)
    print("\n--- two whys per arm ---")
    for r in out:
        print(f"\n{r['label']}:")
        acted = [w for w in r["whys"] if w["acted"]][:1]
        not_acted = [w for w in r["whys"] if not w["acted"]][:1]
        for w in acted + not_acted:
            print(f"  [{'acted' if w['acted'] else 'did not act'}] {w['persona']}: {w['why']}")


if __name__ == "__main__":
    main()
