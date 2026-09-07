"""What does seed (iv) — "an admin puts the mailbox on every recurring dailies" — cost TODAY?

Measured, not estimated: 20 dailies through the only routes the product offers an admin right
now. Each route is timed and its human steps counted.
"""
import json
import os
import time

import simlane

N = int(os.environ.get("SEED_N", "20"))
now = time.time()

print(f"=== route A: one fact per meeting (fact_emit / POST /events), N={N} ===")
t0 = time.time()
ok = 0
for i in range(N):
    sid = f"seed-iv-{int(now)}-{i:03d}"
    st, body = simlane.emit("invite.received", sid, {
        "organizer": f"sim-show{i % 6}-coord@rehearsal.test",
        "url": f"https://meet.jit.si/seediv{int(now)}{i:03d}",
        "start": now + 86400 + i * 60,
        "ics_uid": sid,
        "title": f"Show {chr(65 + i % 6)} dailies",
        "group": None,
        "participants": [f"sim-show{i % 6}-a{j}@rehearsal.test" for j in range(4)],
    })
    if st == 202:
        ok += 1
dt = time.time() - t0
print(f"  admitted {ok}/{N} in {dt:.1f}s  ({dt/N:.2f}s per meeting)")
print(f"  CALLS: {N} — one per meeting. There is no bulk verb.")
json.dump({"n": N, "ok": ok, "seconds": round(dt, 1), "per_meeting": round(dt / N, 2)},
          open(os.path.expanduser("~/sim-runs/r6/route-a.json"), "w"), indent=1)
