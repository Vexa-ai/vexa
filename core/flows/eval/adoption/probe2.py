"""Second probe: the post-meeting leg (capture double -> agent turn -> minutes mail)."""
from __future__ import annotations

import json
import re
import sys
import time

import rig
import simlane
from probe import login

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "sim-probe1@rehearsal.test"
VIDEO = sys.argv[2] if len(sys.argv) > 2 else "1Ph5N_vV230"

uid, tok = login(EMAIL)
me = rig.MCP(tok)
me.init()
native = f"sim-{int(time.time())}"
seed = me.call("meeting_seed", native_id=native, title="Payments platform weekly",
               video_id=VIDEO, timeout=300)
print("seed:", json.dumps(seed)[:400])
if "meeting_id" not in seed:
    raise SystemExit("seed failed")
refs = {"organizer": EMAIL, "url": f"https://meet.google.com/{native}",
        "start": time.time() - 3600, "ics_uid": f"sim-done-{native}",
        "title": "Payments platform weekly", "group": None,
        "meeting_id": seed["meeting_id"], "native": native,
        "transcript": seed["transcript"], "uid": uid}
print("completed:", json.dumps(simlane.emit("meeting.completed", f"sim-done-{native}", refs))[:300])
t0 = time.time()
while time.time() - t0 < 900:
    hit = [m for m in rig.messages_for(EMAIL) if (m.get("Subject") or "").startswith("Minutes:")]
    if hit:
        t = rig.full_touch(hit[0])
        print("\n=== MINUTES MAIL after", round(time.time() - t0), "s ===")
        print(t["subject"]); print(t["text"][:2500]); print("links:", t["links"])
        break
    time.sleep(15)
else:
    print("NO MINUTES MAIL in 900s")
