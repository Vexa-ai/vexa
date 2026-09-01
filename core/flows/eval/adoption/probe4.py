"""probe4 — the SAME experiment probe3 ran, in the sim lane, with the fix in.

probe3 (before): an invite naming three attendees three different ways -> organizer 3 mails,
attendees 0. This runs the post-meeting leg end to end and counts the mails that reach the
attendees. The number this prints IS the before/after of bottleneck #1.

  python3 probe4.py [mode]      mode = shared (default) | personal | off
"""
from __future__ import annotations

import json
import sys
import time

import rig
import simlane
from probe import login

MODE = sys.argv[1] if len(sys.argv) > 1 else "shared"
VIDEO = sys.argv[2] if len(sys.argv) > 2 else "1Ph5N_vV230"

ORG = "sim-spi-coord@rehearsal.test"
ATTENDEES = ["sim-spi-anim@rehearsal.test", "sim-spi-light@rehearsal.test",
             "sim-spi-comp@rehearsal.test"]
OUTSIDE = ["vendor@outside.example"]          # must NEVER be mailed (domain allow-list)

# the lever, as a flow param on a new active version — meta-software, not code
POST_STEPS = ["require_workspace", "process_meeting", "email_minutes", "email_attendees"]
print("flow version:", json.dumps(simlane.flow_params(
    "post_meeting", "meeting.completed", POST_STEPS,
    {"attendee_followup": MODE})[1])[:200])

uid, tok = login(ORG)
me = rig.MCP(tok)
me.init()
me.call("workspace_write", path=".scaffolded",
        content=json.dumps({"ready": True, "at": time.time(), "by": "adoption-sim"}))

native = f"simspi{int(time.time())}"
seed = me.call("meeting_seed", native_id=native, title="Show A Animation dailies",
               video_id=VIDEO, timeout=300)
print("seed:", json.dumps(seed)[:300])
if "meeting_id" not in seed:
    raise SystemExit("seed failed")

sid = f"sim-spi-{native}"
refs = {"organizer": ORG, "url": f"https://meet.google.com/{native}",
        "start": time.time() - 3600, "ics_uid": sid,
        "title": "Show A Animation dailies", "group": None,
        "participants": ATTENDEES + OUTSIDE,
        "meeting_id": seed["meeting_id"], "native": native,
        "transcript": seed["transcript"], "uid": uid}
print("emit:", json.dumps(simlane.emit("meeting.completed", sid, refs))[:300])

r = simlane.wait_reaction(sid, timeout_s=1500)
print("reaction:", json.dumps({k: r.get(k) for k in
                               ("source_event_id", "step", "status", "reason")} if r else None))

print("\n=== MAILBOXES ===")
for addr in [ORG] + ATTENDEES + OUTSIDE:
    ms = rig.messages_for(addr)
    print(f"{addr:34s} -> {len(ms)}: {[m.get('Subject') for m in ms]}")

print("\n=== ONE ATTENDEE FOLLOW-UP, VERBATIM ===")
for m in rig.messages_for(ATTENDEES[0]):
    t = rig.full_touch(m)
    if "what it means for you" in (t["subject"] or ""):
        print(t["subject"]); print(t["text"][:2000]); print("links:", t["links"])
        break
else:
    print("(none)")
