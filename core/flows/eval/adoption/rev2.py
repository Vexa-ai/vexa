"""Revolution 2 — H1 against the null, PER COHORT.

Revolution 1 measured one population against one meeting nobody in it had attended. This runs
the two cohorts separately, each on its own meetings, and reports the lever against the null
inside each:

  insider     the DNA/ASWF working-group world — pipeline engineers and studio technology.
              Fixtures: the recorded DNA TSC sessions.
  production  the people the pilot is for — coordinators, production managers, supervisors,
              artists. Fixtures: generated dailies (synthetic: true), one per show per day.

RE-BASELINE WARNING, and it cuts both ways. This revolution's mails differ from revolution 1's
by more than the cohort: `_readable`, the provenance lines and the note-date fix all landed in
between. An r1→r2 delta therefore attributes to "cohort routing" a change that several edits
share, exactly as the replay worker's own scores would misattribute to their fix what my merge
changed. Only the within-revolution comparisons below (lever vs null, same run, same texts) are
clean.
"""
from __future__ import annotations

import json
import os
import sys

import cohorts
import org as O
import personas as P
import sim as S

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r1"))
SIZES = [int(x) for x in os.environ.get("SIM_SIZES", "2000,20000").split(",")]
LEVERS = ["off", "shared", "personal"]


def cohort_rates(cohort: str):
    path = f"{RUN}/rates-{cohort}.json"
    if not os.path.exists(path):
        return None
    return S.Rates.load(path)


def main():
    results = {}
    print(f"{'cohort':<12}{'size':>8}  {'lever':<10} "
          f"{'T25':>6}{'T50':>6}{'T80':>6}  {'peak':>7}{'steady':>8}  {'ret30':>7}{'ret90':>7}")
    print("-" * 88)
    for cohort in (cohorts.INSIDER, cohorts.PRODUCTION):
        rates = cohort_rates(cohort)
        if rates is None:
            print(f"{cohort:<12} (no rates-{cohort}.json — not sampled)")
            continue
        for n in SIZES:
            o = O.build("spi", n)
            P.assign(o)
            # restrict the population to this cohort: its own people, its own meetings
            keep = set(cohorts.split(o)[cohort])
            o.meetings = [m for m in o.meetings
                          if cohorts.cohort_of_meeting(m) == cohort
                          and (set(m.attendees) & keep)]
            for m in o.meetings:
                m.attendees = sorted(set(m.attendees) & keep) or m.attendees
            o.people = [p for p in o.people if p.pid in keep]
            if not o.people or not o.meetings:
                print(f"{cohort:<12}{n:>8}  (empty after cohort restriction)")
                continue
            for lever in LEVERS:
                r = S.run(o, rates, days=120, attendee_followup=lever)
                results[f"{cohort}|{n}|{lever}"] = {k: v for k, v in r.items() if k != "curve"}
                d = lambda v: (str(v) if v else ">" + str(r["days"]))  # noqa: E731
                print(f"{cohort:<12}{len(o.people):>8}  {lever:<10} "
                      f"{d(r['t_25_days']):>6}{d(r['t_50_days']):>6}{d(r['t_full_days']):>6}  "
                      f"{r['peak_active_share']*100:6.2f}%{r['steady_state_active_share']*100:7.2f}%  "
                      f"{str(r['retention_30']):>7}{str(r['retention_90']):>7}")
        print()
    json.dump(results, open(f"{RUN}/results-rev2.json", "w"), indent=1)
    print(f"written: {RUN}/results-rev2.json")


if __name__ == "__main__":
    sys.exit(main())
