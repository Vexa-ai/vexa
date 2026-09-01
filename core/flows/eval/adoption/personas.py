"""Adoption personas — the simulated humans. The product is real; these are not.

A persona is a short brief handed to Haiku together with the ACTUAL text of a mail the flows
engine sent, plus that persona's own history of recent touches and what it did with them. It
answers one fixed schema. Nothing here encodes a propensity number: the numbers are MEASURED
from the answers, not asserted here — otherwise the simulator would only recite its own priors.

SPI grounding (2026-08-18 DNA dev check-in, Cottalango Leon):
  · the pilot is "actual coordinators and production managers using it as their main tool"
  · "the only feedback that we've gotten is just everyone just wants control … don't do AI,
    don't do AI. And now it's like, no, do it, but make sure I can control every single aspect"
  · dailies run 30 minutes and a coordinator has "a couple of minutes to do everything" before
    the next one — the attention budget is the binding constraint, not goodwill.
"""
from __future__ import annotations

import random

# ── the answer schema ────────────────────────────────────────────────────────────────────────
# ACTIVE (founder, 2026-09-02): "retention is active action — is not just 'emails from vexa
# are coming', it's about emails opening and UI interaction." So `opened` is NOT activity.
# A person is active on a day only if they opened AND did one of the UI actions below.
UI_ACTIONS = ["clicked", "replied", "chat_turn", "invited_own_meeting"]

SCHEMA = {
    "opened": "bool — did you open it at all. Opening is NOT using it.",
    "clicked": "bool — did you follow the link into the Vexa chat (false if there was no link)",
    "chat_turn": "bool — once there, did you actually type something to it (false if you "
                 "landed, glanced and left)",
    "replied": "bool — did you reply to the email itself",
    "completed_setup": "bool — did you finish whatever it asked you to finish",
    "invited_own_meeting": "bool — did you, as a result, put Vexa on a meeting YOU own",
    "forwarded": "bool — did you pass it to a colleague",
    "outcome": "acted | hesitated | ignored",
    "seconds": "int — seconds of your attention it got",
    "friction": "str — the single thing that got in the way, or empty",
    "why": "str — one sentence, first person, why you did that",
}

# name -> (share of headcount, brief)
PERSONAS: dict[str, tuple[float, str]] = {
    "coordinator_under_pressure": (0.14, (
        "You are a production coordinator on a show. You run several dailies a day, each 30 "
        "minutes, and you have about two minutes between them to send notes, update the "
        "tracker and set up the next review. Notes ARE your job — if something writes them "
        "correctly you get an hour of your life back every day, and you will fight for it. "
        "If it writes them wrong you have to check every line, which is slower than typing "
        "them yourself, and you will drop it that same day. You do not have time to read a "
        "long email or to learn a tool on your own time.")),
    "production_manager": (0.06, (
        "You are a production manager. You are accountable for the show landing on schedule, "
        "you sit across several departments, and you are judged on whether things were "
        "communicated. You care about what was decided and who owns it, not about the "
        "transcript. You will adopt something if it makes the show's status legible to you "
        "without you chasing people. You are wary of anything that creates a second place to "
        "look — you already have a tracker.")),
    "artist": (0.55, (
        "You are a department artist — animation, lighting, FX, comp or layout. You sit in "
        "dailies where your shots are reviewed and you take notes on what to fix. You mostly "
        "want to know exactly what was said about YOUR shot, and you resent sitting through "
        "the rest. You are heads-down in your own software most of the day and you check "
        "email between tasks. You will click something that is about your work and ignore "
        "anything that is about a tool. You do not organise meetings.")),
    "supervisor": (0.09, (
        "You are a department supervisor. You give the notes in dailies and you carry the "
        "creative call. You are in meetings most of the day. You are protective of how your "
        "notes are represented — a summary that paraphrases your direction badly is worse "
        "than no summary, because the artist will act on it. You will use something that "
        "reproduces what you actually said, and you will publicly reject something that "
        "garbles it.")),
    "pipeline_engineer": (0.10, (
        "You are a pipeline or studio-technology engineer. You have opinions about tools and "
        "you try them before anyone asks. You immediately want to know where the data goes, "
        "whether there is an API, and whether it can be self-hosted. You are the person "
        "colleagues ask before adopting anything. If it works you will tell your team the "
        "same day, and you will wire it into something.")),
    "control_wary": (0.04, (
        "You have been through the studio's AI conversation from both sides. Your position is "
        "the one the studio settled on: not 'don't do AI' any more, but 'do it, and let me "
        "control every single aspect of the pipeline'. Before you let anything transcribe a "
        "review you want to know who can read the output, whether you can exclude a meeting, "
        "and whether you can delete it. A clear statement that the creator controls sharing "
        "and can exclude a meeting moves you; silence on it stops you completely. The show is "
        "under NDA and that is not a formality to you.")),
    "overloaded_exec": (0.02, (
        "You are a studio executive. You have eight meetings a day and two hundred unread "
        "mails. You triage by subject line and your assistant handles most of your inbox. You "
        "will act on something only if it takes under thirty seconds and removes work rather "
        "than adding it. You never complete a setup flow. You do forward things to the person "
        "who should handle them.")),
}

# Role skews: multiplicative weights applied to the base mix before sampling.
ROLE_SKEW = {
    "coordinator":        {"coordinator_under_pressure": 14.0, "artist": 0.02,
                           "production_manager": 1.0, "supervisor": 0.05},
    "production_manager": {"production_manager": 14.0, "artist": 0.02,
                           "coordinator_under_pressure": 1.5, "supervisor": 0.1},
    "supervisor":         {"supervisor": 12.0, "artist": 0.3, "control_wary": 1.6,
                           "coordinator_under_pressure": 0.1},
    "artist":             {"artist": 3.0, "coordinator_under_pressure": 0.02,
                           "production_manager": 0.02, "supervisor": 0.05,
                           "pipeline_engineer": 0.25, "overloaded_exec": 0.02},
    "engineer":           {"pipeline_engineer": 12.0, "artist": 0.05, "control_wary": 1.5,
                           "coordinator_under_pressure": 0.02},
    "lead":               {"pipeline_engineer": 4.0, "production_manager": 2.0,
                           "control_wary": 1.6, "artist": 0.2},
    "exec":               {"overloaded_exec": 20.0, "artist": 0.02, "control_wary": 1.4},
    "staff":              {"artist": 0.2, "control_wary": 2.0, "production_manager": 1.0,
                           "coordinator_under_pressure": 0.3},
    # bank profile's role vocabulary, kept so the second profile still assigns
    "manager":            {"production_manager": 6.0, "artist": 0.1},
    "ic":                 {"artist": 2.0, "pipeline_engineer": 0.6},
    "assistant":          {"coordinator_under_pressure": 4.0, "artist": 0.3},
}

# Departments where control and NDA are loudest, and where curiosity is highest.
DEPT_SKEW = {
    "Pipeline & Engineering": {"pipeline_engineer": 2.0, "control_wary": 1.4},
    "Studio Technology":      {"pipeline_engineer": 2.0, "control_wary": 1.6},
    "Finance & Legal":        {"control_wary": 4.0},
    "HR & Recruiting":        {"control_wary": 3.0},
    "IT & Facilities":        {"control_wary": 2.0, "pipeline_engineer": 1.5},
    "Studio Executive":       {"overloaded_exec": 3.0, "control_wary": 1.5},
    "Editorial":              {"artist": 1.5},
    # bank
    "Compliance": {"control_wary": 2.4}, "Legal": {"control_wary": 2.6},
    "Human Resources": {"control_wary": 2.6}, "Internal Audit": {"control_wary": 2.0},
    "Data & Analytics": {"pipeline_engineer": 2.4}, "IT Infrastructure": {"pipeline_engineer": 2.2},
}

NAMES = list(PERSONAS)


def assign(org, seed: int = 11) -> None:
    """Give every person a persona. Mutates org.people in place; pure in the seed."""
    rng = random.Random(seed)
    for p in org.people:
        w = []
        for nm in NAMES:
            base = PERSONAS[nm][0]
            base *= ROLE_SKEW.get(p.role, {}).get(nm, 1.0)
            base *= DEPT_SKEW.get(p.dept, {}).get(nm, 1.0)
            w.append(base)
        p.persona = rng.choices(NAMES, weights=w)[0]


def mix(org) -> dict:
    out: dict[str, int] = {}
    for p in org.people:
        out[p.persona] = out.get(p.persona, 0) + 1
    n = len(org.people)
    return {k: round(v / n, 3) for k, v in sorted(out.items(), key=lambda x: -x[1])}


if __name__ == "__main__":
    import json
    import sys

    import org as O
    o = O.build(sys.argv[1] if len(sys.argv) > 1 else "spi")
    assign(o)
    print(json.dumps(mix(o), indent=1))
    by_role: dict[str, dict[str, int]] = {}
    for p in o.people:
        by_role.setdefault(p.role, {}).setdefault(p.persona, 0)
        by_role[p.role][p.persona] += 1
    print(json.dumps(by_role, indent=1))
