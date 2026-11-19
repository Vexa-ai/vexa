"""Build `table_by_history` — the rates keyed the way they were actually SAMPLED.

sample.py records one row per valid answer in `whys` (persona, history, opened, active_action,
friction, why). The flat `table` averages the three history states together; sim.py needs them
separate, because "would you open the third thing you have ignored" is precisely the question
retention turns on, and it was measured rather than assumed.

Run as a post-processor on an existing rates.json; also patches sample.py so the next
revolution emits it directly.
"""
import json
import os
import sys
from collections import defaultdict

def jeffreys(k: int, n: int) -> float:
    """Beta(1/2,1/2) posterior mean. A measured 0/14 becomes 3.3%, not 0% — low, but not
    impossible, which is all a sample that size can actually support. Without this, every zero
    cell is an absorbing state and no lever can be ranked against any other."""
    if n <= 0:
        return 0.0
    return (k + 0.5) / (n + 1.0)


RUN = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/sim-runs/r1")
path = f"{RUN}/rates.json"
d = json.load(open(path))

agg = defaultdict(lambda: {"n": 0, "open": 0, "act": 0})
for kind, rows in d.get("whys", {}).items():
    for r in rows:
        k = (r["persona"], kind, r["history"])
        g = agg[k]
        g["n"] += 1
        if r.get("opened"):
            g["open"] += 1
            g["act"] += bool(r.get("active_action"))

flat = d.get("table", {})
by_hist = {}
for (persona, kind, hist), g in agg.items():
    if not g["n"]:
        continue
    base = flat.get(f"{persona}|{kind}", {})
    by_hist[f"{persona}|{kind}|{hist}"] = {
        "n": g["n"],
        "open": round(jeffreys(g["open"], g["n"]), 4),
        "open_raw": round(g["open"] / g["n"], 4),
        "act_given_open": round(jeffreys(g["act"], g["open"]), 4),
        "act_given_open_raw": round(g["act"] / g["open"], 4) if g["open"] else 0.0,
        "invite": max(base.get("invite", 0.0), 0.01),
        "forward": max(base.get("forward", 0.0), 0.01),
    }

# A `*` fallback per (kind, history) so a thin cell borrows from the whole population rather
# than from a hard-coded default — the default is the last resort, not the first.
pop = defaultdict(lambda: {"n": 0, "open": 0, "act": 0})
for kind, rows in d.get("whys", {}).items():
    for r in rows:
        g = pop[(kind, r["history"])]
        g["n"] += 1
        if r.get("opened"):
            g["open"] += 1
            g["act"] += bool(r.get("active_action"))
for (kind, hist), g in pop.items():
    by_hist[f"*|{kind}|{hist}"] = {
        "n": g["n"],
        "open": round(jeffreys(g["open"], g["n"]), 4),
        "open_raw": round(g["open"] / g["n"], 4),
        "act_given_open": round(jeffreys(g["act"], g["open"]), 4),
        "act_given_open_raw": round(g["act"] / g["open"], 4) if g["open"] else 0.0,
        "invite": 0.02, "forward": 0.02,
    }

d["table_by_history"] = by_hist
json.dump(d, open(path, "w"), indent=1)
print(f"table_by_history: {len(by_hist)} cells "
      f"({sum(v['n'] for k, v in by_hist.items() if not k.startswith('*'))} answers)")
for k in sorted(by_hist):
    if k.startswith("*"):
        v = by_hist[k]
        print(f"  {k:34s} n={v['n']:3d} open={v['open']:.2f} (raw {v['open_raw']:.2f})"
              f"  act|open={v['act_given_open']:.2f} (raw {v['act_given_open_raw']:.2f})")
