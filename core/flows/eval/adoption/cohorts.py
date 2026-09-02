"""Cohorts — which meetings a person could plausibly be IN, and therefore be written to about.

Revolution 1's §4 was contaminated by the instrument, not by the product: SPI coordinators,
artists and supervisors were handed the minutes of an ASWF TSC governance meeting they had
never attended, and ignored them for exactly that — "not my show, not my dailies", "not a word
about my shots". That is the harness attaching the wrong meeting to the wrong person. A touch
about a meeting you were not in cannot measure whether the product earns action; it only
measures whether people notice they were not there. They do.

So every fixture carries a COHORT, every person belongs to one, and a touch is only ever
generated for a person whose cohort matches the meeting.

  insider     the DNA/ASWF working-group world — pipeline engineers, studio technology, TDs,
              the Cottalango/Olga-shaped people who are actually in the TSC. The recorded DNA
              fixtures belong here, and to nobody else.
  production  the people the pilot is actually for — coordinators, production managers,
              supervisors, department artists. Their meeting is DAILIES, per show, per
              department, every day. No recording of one exists, so the fixtures for this cohort
              are generated (see dailies.py) and marked `synthetic: true`.
"""
from __future__ import annotations

INSIDER = "insider"
PRODUCTION = "production"

# role -> cohort. The org generator's own role vocabulary; the bank profile's roles map too so
# the second profile still routes.
ROLE_COHORT = {
    "engineer": INSIDER,
    "lead": INSIDER,
    "coordinator": PRODUCTION,
    "production_manager": PRODUCTION,
    "supervisor": PRODUCTION,
    "artist": PRODUCTION,
    "exec": PRODUCTION,
    "staff": PRODUCTION,
    # bank
    "manager": PRODUCTION,
    "ic": PRODUCTION,
    "assistant": PRODUCTION,
}

# meeting kind -> cohort whose members are the ones actually in the room
KIND_COHORT = {
    "tsc": INSIDER,
    "dev_checkin": INSIDER,
    "dailies": PRODUCTION,
    "production_meeting": PRODUCTION,
    "show_review": PRODUCTION,
    "one_on_one": PRODUCTION,
    "team_weekly": PRODUCTION,
    "dept_leadership": PRODUCTION,
    "exec_staff": PRODUCTION,
    "standup": PRODUCTION,
    "project": PRODUCTION,
    "client_review": PRODUCTION,
    "external": PRODUCTION,
}


def cohort_of_person(p) -> str:
    dept = getattr(p, "dept", "") or ""
    if "Engineering" in dept or "Technology" in dept:
        return INSIDER
    return ROLE_COHORT.get(getattr(p, "role", ""), PRODUCTION)


def cohort_of_meeting(m) -> str:
    return KIND_COHORT.get(getattr(m, "kind", ""), PRODUCTION)


def split(org) -> dict:
    out = {INSIDER: [], PRODUCTION: []}
    for p in org.people:
        out[cohort_of_person(p)].append(p.pid)
    return out


def stats(org) -> dict:
    s = split(org)
    mk: dict = {}
    for m in org.meetings:
        c = cohort_of_meeting(m)
        mk[c] = mk.get(c, 0) + 1
    return {"people": {k: len(v) for k, v in s.items()}, "meeting_series": mk}


if __name__ == "__main__":
    import json
    import sys

    import org as O
    import personas as P
    o = O.build(sys.argv[1] if len(sys.argv) > 1 else "spi",
                int(sys.argv[2]) if len(sys.argv) > 2 else 2000)
    P.assign(o)
    st = stats(o)
    print(json.dumps(st, indent=1))
    by = {}
    for p in o.people:
        by.setdefault(cohort_of_person(p), {}).setdefault(p.persona, 0)
        by[cohort_of_person(p)][p.persona] += 1
    print(json.dumps(by, indent=1))
