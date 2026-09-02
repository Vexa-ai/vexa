"""Layer 1 — the SAMPLE on the real stack. Produces `rates.json` for sim.py.

Three phases, and only the first two are cheap:

  harvest   pull the ACTUAL mails the flows engine sent to the sim identities out of mailpit.
            Nothing here is written by hand: a touch the product does not send cannot be
            sampled, which is why the attendee follow-up had to be BUILT before it could be
            measured.
  judge     each (persona × touch_kind × history-state) -> one Haiku call reading that real text.
            Produces the open / act-given-open rates sim.py extrapolates on.
  converse  where a persona CLICKED, the touch does not end: it has a real conversation with
            the REAL agent in that identity's chat, through agent-api /api/chat, bounded at 3
            persona turns. Haiku on both sides — the persona is simulated, the agent is the
            product as deployed. Scored on: did the first agent turn TELL or ask, turns to
            value, did an asked action actually happen, and why anyone abandoned.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

import judge
import cohorts
import personas
import rig

AGENT_API = os.environ.get("SIM_AGENT_API", "http://127.0.0.1:18500")
RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r1"))
COHORT = os.environ.get("SIM_COHORT", cohorts.INSIDER)

# How a real mail becomes a touch kind sim.py reasons about.
# SUBJECT ALONE IS NOT ENOUGH: variant A and variant B of the attendee follow-up carry the
# IDENTICAL subject line, so a subject-only matcher silently sampled one mail as both and would
# have reported the two variants as indistinguishable. Each kind names the mailbox it is
# sourced from — the run that produced it — as well as the subject test.
# Each touch kind is sourced from the mailbox of the run that produced it, AND belongs to a
# COHORT — the group of people who would actually have been in that meeting.
#
# Revolution 1 judged every persona against one ASWF TSC governance meeting. Coordinators,
# artists and supervisors ignored it and said why: "not my show, not my dailies", "not a word
# about my shots". That is the instrument attaching the wrong meeting to the wrong person; a
# touch about a meeting you were not in measures whether people notice they were not there,
# which they do. So insider personas judge the DNA/TSC mails, production personas judge the
# dailies mails, and neither is ever shown the other's.
COHORT_SOURCES = {
    cohorts.INSIDER: [
        ("prepare",           "Prepare:",              ["sim-dnaA-coord@rehearsal.test",
                                                        "sim-probe1@rehearsal.test"]),
        ("minutes",           "Minutes:",              ["sim-dnaA-coord@rehearsal.test"]),
        ("attendee_shared",   "what it means for you", ["sim-dnaA-eng1@rehearsal.test"]),
        ("attendee_personal", "what it means for you", ["sim-dnaB-eng1@rehearsal.test"]),
    ],
    cohorts.PRODUCTION: [
        ("prepare",           "Prepare:",              ["sim-prod-coord@rehearsal.test",
                                                        "sim-dnaA-coord@rehearsal.test"]),
        ("minutes",           "Minutes:",              ["sim-prod-coord@rehearsal.test"]),
        ("attendee_shared",   "what it means for you", ["sim-prod-eng1@rehearsal.test"]),
        ("attendee_personal", "what it means for you", ["sim-prod-eng2@rehearsal.test",
                                                        "sim-prod-eng1@rehearsal.test"]),
    ],
}

# which personas belong to which cohort, for judging
PERSONA_COHORT = {
    "pipeline_engineer": cohorts.INSIDER,
    "coordinator_under_pressure": cohorts.PRODUCTION,
    "production_manager": cohorts.PRODUCTION,
    "supervisor": cohorts.PRODUCTION,
    "artist": cohorts.PRODUCTION,
    "control_wary": cohorts.PRODUCTION,
    "overloaded_exec": cohorts.PRODUCTION,
}

SOURCES = COHORT_SOURCES[cohorts.INSIDER]

HISTORY_STATES = {
    "fresh": [],
    "one_ignore": [{"day": 3, "kind": "prepare", "outcome": "ignored",
                    "why": "I did not have time and it was not obviously about my shots"}],
    "two_ignores": [{"day": 3, "kind": "prepare", "outcome": "ignored",
                     "why": "I did not have time"},
                    {"day": 5, "kind": "minutes", "outcome": "ignored",
                     "why": "the summary was generic — it did not say anything I did not know"}],
}


class FakePerson:
    """A person the judge can read, without needing the whole org built."""
    def __init__(self, persona, role, dept, name):
        self.persona, self.role, self.dept, self.name = persona, role, dept, name


ROLE_FOR = {
    "coordinator_under_pressure": ("coordinator", "Show A"),
    "production_manager": ("production_manager", "Show A"),
    "artist": ("artist", "Show A"),
    "supervisor": ("supervisor", "Show A"),
    "pipeline_engineer": ("engineer", "Pipeline & Engineering"),
    "control_wary": ("staff", "Finance & Legal"),
    "overloaded_exec": ("exec", "Studio Executive"),
}


def harvest(sources=None) -> dict:
    """kind -> the NEWEST real mail of that kind, from the mailbox that run wrote to.
    mailpit returns newest first, so the first match is the most recent product behaviour —
    which matters, because the body changed under us when `_readable` landed."""
    found = {}
    for kind, needle, addrs in (sources or SOURCES):
        for a in addrs:
            if kind in found:
                break
            for m in rig.messages_for(a):
                if needle not in (m.get("Subject") or ""):
                    continue
                t = rig.full_touch(m)
                if t["text"].strip():
                    found[kind] = t
                    break
    return found


def judge_phase(touches: dict, reps: int = 2, cohort: str | None = None) -> dict:
    jobs, keys = [], []
    for persona in personas.PERSONAS:
        if cohort and PERSONA_COHORT.get(persona) != cohort:
            continue                      # never judge a persona on a meeting it was not in
        role, dept = ROLE_FOR.get(persona, ("artist", "Show A"))
        who = FakePerson(persona, role, dept, "Sam Okafor")
        for kind, touch in touches.items():
            for hname, hist in HISTORY_STATES.items():
                for r in range(reps):
                    load = 1 if hname == "fresh" else 4
                    jobs.append((who, touch, hist, 10, load))
                    keys.append((persona, kind, hname))
    print(f"judging {len(jobs)} touches on Haiku…", flush=True)
    answers = judge.decide_many(jobs, workers=10)

    raw = defaultdict(list)
    for k, a in zip(keys, answers):
        raw[k].append(a)

    table, whys, errs = {}, defaultdict(list), 0
    agg = defaultdict(lambda: {"n": 0, "open": 0, "act": 0, "inv": 0, "fwd": 0})
    for (persona, kind, hname), answers_ in raw.items():
        for a in answers_:
            if a.get("_error"):
                errs += 1
                continue
            g = agg[(persona, kind)]
            g["n"] += 1
            g["open"] += bool(a.get("opened"))
            if a.get("opened"):
                g["act"] += bool(a.get("active_action"))
                g["inv"] += bool(a.get("invited_own_meeting"))
                g["fwd"] += bool(a.get("forwarded"))
            if a.get("why"):
                whys[kind].append({"persona": persona, "history": hname,
                                   "outcome": a.get("outcome"), "opened": a.get("opened"),
                                   "active_action": a.get("active_action"),
                                   "friction": a.get("friction", ""), "why": a["why"]})
    for (persona, kind), g in agg.items():
        if not g["n"]:
            continue
        op = g["open"] / g["n"]
        table[f"{persona}|{kind}"] = {
            "n": g["n"], "open": round(op, 4),
            "act_given_open": round(g["act"] / g["open"], 4) if g["open"] else 0.0,
            "invite": round(g["inv"] / g["open"], 4) if g["open"] else 0.0,
            "forward": round(g["fwd"] / g["open"], 4) if g["open"] else 0.0,
        }
    return {"table": table, "whys": dict(whys), "judge_errors": errs}


# ── phase 3: the conversation with the real agent ────────────────────────────────────────────
def agent_turn(uid: str, session: str, prompt: str, timeout=180) -> str:
    """One REAL turn against the deployed agent, over the SSE stream agent-api serves."""
    import urllib.request
    req = urllib.request.Request(
        f"{AGENT_API}/api/chat", method="POST",
        data=json.dumps({"prompt": prompt, "session": session}).encode())
    req.add_header("content-type", "application/json")
    req.add_header("X-User-Id", str(uid))
    reply = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:  # noqa: BLE001
                    continue
                if ev.get("type") == "done":
                    reply = ev.get("reply") or reply
    except Exception as e:  # noqa: BLE001
        return f"(no answer: {type(e).__name__})"
    return reply


def converse(person, uid: str, session: str, opening_prompt: str, max_turns: int = 3) -> dict:
    """The persona and the product, alternating. Both sides recorded verbatim."""
    log, actions = [], []
    opening = agent_turn(uid, session, opening_prompt)
    log.append({"who": "agent", "text": opening})
    d = judge.conv_open(person, opening)
    log.append({"who": "person", "text": d.get("say", ""), "judgment": d})
    turns_to_value = 1 if d.get("got_value") else None
    if d.get("asked_action") and d["asked_action"] != "none":
        actions.append(d["asked_action"])
    n = 1
    while not d.get("abandoned") and d.get("say") and n < max_turns:
        ans = agent_turn(uid, session, d["say"])
        log.append({"who": "agent", "text": ans})
        d = judge.conv_turn(person, d["say"], ans)
        log.append({"who": "person", "text": d.get("say", ""), "judgment": d})
        n += 1
        if turns_to_value is None and d.get("got_value"):
            turns_to_value = n
        if d.get("asked_action") and d["asked_action"] != "none":
            actions.append(d["asked_action"])
    first_agent = (log[0]["text"] or "")
    return {
        "persona": person.persona,
        "turns": n,
        "turns_to_value": turns_to_value,
        "got_value": bool(turns_to_value),
        "abandoned": bool(d.get("abandoned")),
        "abandon_why": d.get("why", "") if d.get("abandoned") else "",
        "asked_actions": actions,
        # the preset rule: the opening must TELL from the workspace, then ask ONE question
        "opened_by_telling": not first_agent.strip().endswith("?")
                             and first_agent.count("?") <= 1,
        "questions_in_opening": first_agent.count("?"),
        "log": log,
    }


def main():
    os.makedirs(RUN, exist_ok=True)
    touches = harvest(COHORT_SOURCES.get(COHORT, SOURCES))
    print("harvested touch kinds:", sorted(touches))
    json.dump(touches, open(f"{RUN}/touches-{COHORT}.json", "w"), indent=1)
    if not touches:
        raise SystemExit("no real touches harvested — nothing to judge")
    out = judge_phase(touches, cohort=COHORT)
    out["harvested"] = sorted(touches)
    out["cohort"] = COHORT
    out["history_penalty"] = 0.72
    out["fatigue_penalty"] = 0.65
    json.dump(out, open(f"{RUN}/rates-{COHORT}.json", "w"), indent=1)
    print(json.dumps(out["table"], indent=1)[:3000])
    print("judge errors:", out["judge_errors"])


if __name__ == "__main__":
    main()
