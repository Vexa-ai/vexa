"""The size table: size × T_full × retention × dominant bottleneck × best lever, and sign flips.

Bottleneck is DERIVED from the run, never asserted: at each size the stage that loses the most
people is named by comparing what the run recorded — how many were ever reached, how many ever
acted, how many ever put the mailbox on a meeting of their own, and what the churn reasons say.
"""
from __future__ import annotations

import json
import os
import sys

import cohorts
import org as O
import personas as P
import sim as S

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r4"))
SIZES = [int(x) for x in os.environ.get("SIM_SIZES", "2000,20000,200000").split(",")]
LEVERS = ["off", "shared", "personal"]
PROFILE_NAME = {2000: "SPI", 20000: "Sony Pictures Entertainment", 200000: "Sony Group"}


def bottleneck(r: dict) -> str:
    """Which stage loses the most, from the run's own counters."""
    reach = r["ever_reached"]
    act = r["ever_active"]
    inv = r["invited_mailbox"]
    pool = r["reachable"]
    stages = [
        ("seeding — almost nobody is ever touched", 1.0 - (reach / pool if pool else 0)),
        ("the touch — reached but never acts", (reach - act) / reach if reach else 0),
        ("the second invite — active but never invites their own", 
         (act - inv) / act if act else 0),
    ]
    stages.sort(key=lambda x: -x[1])
    return f"{stages[0][0]} ({stages[0][1]*100:.0f}% lost)"


def main():
    rates = {c: (S.Rates.load(f"{RUN}/rates-{c}.json")
                 if os.path.exists(f"{RUN}/rates-{c}.json") else None)
             for c in (cohorts.INSIDER, cohorts.PRODUCTION)}
    rows, results = [], {}
    for n in SIZES:
        o = O.build("spi", n)
        P.assign(o)
        for cohort in (cohorts.INSIDER, cohorts.PRODUCTION):
            R = rates.get(cohort)
            if R is None:
                continue
            keep = set(cohorts.split(o)[cohort])
            oc = O.Org([p for p in o.people if p.pid in keep],
                       [m for m in o.meetings if cohorts.cohort_of_meeting(m) == cohort
                        and (set(m.attendees) & keep)], o.teams, o.profile)
            for m in oc.meetings:
                m.attendees = sorted(set(m.attendees) & keep) or m.attendees
            if not oc.people or not oc.meetings:
                continue
            best, best_r = None, None
            per_lever = {}
            for lever in LEVERS:
                r = S.run(oc, R, days=120, attendee_followup=lever)
                per_lever[lever] = r
                results[f"{n}|{cohort}|{lever}"] = {k: v for k, v in r.items() if k != "curve"}
                if best_r is None or r["steady_state_active_share"] > best_r["steady_state_active_share"]:
                    best, best_r = lever, r
            null = per_lever["off"]
            rows.append({
                "size": n, "profile": PROFILE_NAME.get(n, str(n)), "cohort": cohort,
                "n": len(oc.people),
                "t_full": best_r["t_full_days"], "t50": best_r["t_50_days"],
                "t25": best_r["t_25_days"], "days": best_r["days"],
                "steady": best_r["steady_state_active_share"],
                "ret30": best_r["retention_30"], "ret90": best_r["retention_90"],
                "best_lever": best,
                "lift_vs_null": (round(best_r["steady_state_active_share"] /
                                       null["steady_state_active_share"], 1)
                                 if null["steady_state_active_share"] else None),
                "bottleneck": bottleneck(best_r),
                "per_lever_steady": {k: v["steady_state_active_share"] for k, v in per_lever.items()},
            })
            print(f"{n:>7} {PROFILE_NAME.get(n,''):<28} {cohort:<11} n={len(oc.people):<6} "
                  f"T25={str(rows[-1]['t25'] or '>'+str(rows[-1]['days'])):<5} "
                  f"T80={str(rows[-1]['t_full'] or '>'+str(rows[-1]['days'])):<5} "
                  f"steady={rows[-1]['steady']*100:5.2f}%  ret30={rows[-1]['ret30']} "
                  f"ret90={rows[-1]['ret90']}  best={best}  x{rows[-1]['lift_vs_null']}", flush=True)
            print(f"        bottleneck: {rows[-1]['bottleneck']}", flush=True)

    # sign flips: does the ORDER of the levers change with size, inside a cohort?
    print("\n=== SIGN FLIPS ===")
    for cohort in (cohorts.INSIDER, cohorts.PRODUCTION):
        seq = [(r["size"], sorted(r["per_lever_steady"], key=lambda k: -r["per_lever_steady"][k]))
               for r in rows if r["cohort"] == cohort]
        if not seq:
            continue
        orders = {tuple(o) for _, o in seq}
        if len(orders) == 1:
            print(f"  {cohort}: none — lever order is {' > '.join(seq[0][1])} at every size")
        else:
            for size, o in seq:
                print(f"  {cohort} @ {size}: {' > '.join(o)}")
    json.dump(rows, open(f"{RUN}/size-table.json", "w"), indent=1)
    print(f"\nwritten: {RUN}/size-table.json")


if __name__ == "__main__":
    sys.exit(main())
