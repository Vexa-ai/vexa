"""Revolution 5A — the SEED table: does WHO you onboard first change T_full?

Everything else is held fixed: the personal follow-up, the offer in mail and chat, the measured
rates. Only day-0 membership changes. The question the table answers is the pilot-design one —
"who do we onboard first" — and the number that matters beside T_full is what FRACTION of the org
the seed required, because a strategy that reaches everyone by seeding everyone has answered
nothing.
"""
from __future__ import annotations

import json
import os
import sys

import cohorts
import org as O
import personas as P
import sim as S

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r5"))
SIZES = [int(x) for x in os.environ.get("SIM_SIZES", "2000,20000,200000").split(",")]
PROFILE = {2000: "SPI", 20000: "SPE", 200000: "Sony Group"}
STRATS = ["pilot_random", "one_coordinator_per_show", "all_coordinators_and_pms",
          "admin_all_dailies"]
LEVER = os.environ.get("SIM_LEVER", "personal")
PRESENCE = float(os.environ.get("SIM_PRESENCE", "0"))


def main():
    os.makedirs(RUN, exist_ok=True)
    rates = S.Rates.load(f"{RUN}/rates-production.json")
    rows = []
    print(f"lever={LEVER}  presence_lift={PRESENCE}\n")
    print(f"{'size':>8} {'profile':<11} {'seed strategy':<26} {'seed':>7} {'seed%':>7} "
          f"{'T25':>6}{'T50':>6}{'T80':>6} {'peak':>7} {'steady':>7} {'ret30':>7}{'ret90':>7}")
    print("-" * 112)
    for n in SIZES:
        o = O.build("spi", n)
        P.assign(o)
        keep = set(cohorts.split(o)[cohorts.PRODUCTION])
        oc = O.Org([p for p in o.people if p.pid in keep],
                   [m for m in o.meetings
                    if cohorts.cohort_of_meeting(m) == cohorts.PRODUCTION
                    and (set(m.attendees) & keep)], o.teams, o.profile)
        for m in oc.meetings:
            m.attendees = sorted(set(m.attendees) & keep) or m.attendees
        for strat in STRATS:
            r = S.run(oc, rates, days=120, attendee_followup=LEVER,
                      seed_strategy=strat, presence_lift=PRESENCE)
            rows.append({"size": n, "profile": PROFILE.get(n, str(n)), "strategy": strat, **{
                k: v for k, v in r.items() if k != "curve"}})

            def d(v):
                return str(v) if v else ">" + str(r["days"])
            print(f"{n:>8} {PROFILE.get(n,''):<11} {strat:<26} {r['seed_size']:>7} "
                  f"{r['seed_share']*100:>6.2f}% {d(r['t_25_days']):>6}{d(r['t_50_days']):>6}"
                  f"{d(r['t_full_days']):>6} {r['peak_active_share']*100:>6.2f}% "
                  f"{r['steady_state_active_share']*100:>6.2f}% "
                  f"{str(r['retention_30']):>7}{str(r['retention_90']):>7}", flush=True)
        print()
    json.dump(rows, open(f"{RUN}/seed-table{'-presence' if PRESENCE else ''}.json", "w"), indent=1)

    daysy = [r for r in rows if r["t_full_days"] or r["t_50_days"]]
    print("=== does any strategy bring T_full into DAYS? ===")
    if not daysy:
        print("  no. no strategy crosses even 50% inside 120 days at any size.")
        best = max(rows, key=lambda r: r["steady_state_active_share"])
        print(f"  best steady state: {best['strategy']} @ {best['size']} = "
              f"{best['steady_state_active_share']*100:.2f}% on a seed of "
              f"{best['seed_size']} ({best['seed_share']*100:.2f}% of the org)")
    else:
        for r in daysy:
            print(f"  {r['strategy']} @ {r['size']}: T50={r['t_50_days']} T80={r['t_full_days']} "
                  f"on a seed of {r['seed_size']} ({r['seed_share']*100:.2f}% of the org)")


if __name__ == "__main__":
    sys.exit(main())
