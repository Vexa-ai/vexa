"""Bottleneck probe: does ANY touch ever reach an attendee who is not the organizer?

The claim under test (PRD §16.1): "the attendee never hears from the product". We emit an
invite that explicitly names three attendees in refs — the most generous possible input — and
then watch all four mailboxes.
"""
from __future__ import annotations

import json
import time

import rig
import simlane
from probe import login

ORG = "sim-org-a@rehearsal.test"
ATTENDEES = ["sim-att-a1@rehearsal.test", "sim-att-a2@rehearsal.test",
             "sim-att-a3@rehearsal.test"]

uid, tok = login(ORG)
me = rig.MCP(tok)
me.init()
me.call("workspace_write", path=".scaffolded",
        content=json.dumps({"ready": True, "at": time.time(), "by": "adoption-sim"}))

native = f"simatt{int(time.time())}"
stamp = int(time.time())
refs = {"organizer": ORG, "url": f"https://meet.google.com/{native}",
        "start": time.time() + 30 * 86400, "ics_uid": f"sim-att-{stamp}",
        "title": "Risk model review", "group": None,
        # the most generous input the schema could carry — three ways of naming them
        "participants": ATTENDEES, "attendees": ATTENDEES,
        "ATTENDEE": ATTENDEES}
print("invite:", json.dumps(simlane.emit("invite.received", refs["ics_uid"], refs)))
time.sleep(60)
for addr in [ORG] + ATTENDEES:
    subs = [m.get("Subject") for m in rig.messages_for(addr)]
    print(f"{addr:34s} -> {len(subs)} mail(s): {subs}")
