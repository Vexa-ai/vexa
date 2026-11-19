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
# The fixture has to MATCH the meeting it is presented as. The first judged touch caught
# this: a Kubernetes SIG transcript mailed under an "Animation dailies" title was ignored
# for the mismatch, so the sample would have measured our fixture, not our product.
VIDEO = sys.argv[2] if len(sys.argv) > 2 else "dna-2026-03-02"
TITLE = sys.argv[3] if len(sys.argv) > 3 else "DNA dailies-notes working session"
TAG = sys.argv[4] if len(sys.argv) > 4 else MODE[:4]

ORG = f"sim-{TAG}-coord@rehearsal.test"
ATTENDEES = [f"sim-{TAG}-eng1@rehearsal.test", f"sim-{TAG}-eng2@rehearsal.test",
             f"sim-{TAG}-sup1@rehearsal.test"]
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

native = f"sim{TAG}{int(time.time())}"
seed = me.call("meeting_seed", native_id=native, title=TITLE,
               video_id=VIDEO, timeout=300)
print("seed:", json.dumps(seed)[:300])
if "meeting_id" not in seed:
    raise SystemExit("seed failed")

# meeting_seed stopped handing back the transcript (rig fc1da36ce) — the agent is meant to read
# it itself through the MCP rather than receive a copy truncated to fit inside an event. The
# flows step still formats refs["transcript"] into its prompt, so the harness reads it here and
# passes it; when that step moves to an identity-only event this line goes away with it.
tx = me.call("meeting_transcript", meeting_id=str(seed["meeting_id"]), tail=0, timeout=300)
transcript = ""
if isinstance(tx, dict):
    body = tx.get("transcript")
    if isinstance(body, list):           # [{who, said}, ...] — the rig's shape, not a string
        transcript = "\n".join(f"{seg.get('who', '?')}: {seg.get('said', '')}"
                                for seg in body)
    elif isinstance(body, str):
        transcript = body
print("transcript chars:", len(transcript))

sid = f"sim-{TAG}-{native}"
refs = {"organizer": ORG, "url": f"https://meet.google.com/{native}",
        "start": time.time() - 3600, "ics_uid": sid,
        "title": TITLE, "group": None,
        "participants": ATTENDEES + OUTSIDE,
        "meeting_id": seed["meeting_id"], "native": native,
        "transcript": transcript, "uid": uid}
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
