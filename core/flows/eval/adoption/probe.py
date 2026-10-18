"""One-identity smoke probe: does the real touch chain fire for a sim identity at all?

Run before the sample. Every leg it exercises is a leg the sampler depends on.
"""
from __future__ import annotations

import json
import re
import sys
import time

import rig
import simlane

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "sim-probe@rehearsal.test"


def login(email: str) -> tuple[str, str]:
    anon = rig.MCP()
    anon.init()
    r = anon.call("start_onboarding", email=email)
    print("start_onboarding:", json.dumps(r)[:300])
    m = rig.wait_for(email, lambda x: "code" in (x.get("Subject") or "").lower()
                     or "sign" in (x.get("Subject") or "").lower(), timeout_s=90)
    if not m:
        raise SystemExit(f"no code mail reached {email}")
    t = rig.full_touch(m)
    print("code mail subject:", t["subject"])
    code = re.search(r"\b(\d{6})\b", t["text"])
    if not code:
        raise SystemExit("no 6-digit code in: " + t["text"][:400])
    r = anon.call("confirm_login", email=email, code=code.group(1))
    tok = r.get("token") or ""
    print("confirm_login uid:", r.get("uid"), "token:", bool(tok))
    return r.get("uid", ""), tok


def main():
    uid, tok = login(EMAIL)
    if not tok:
        raise SystemExit("no token")
    me = rig.MCP(tok)
    me.init()
    print("settings:", json.dumps(me.call("settings"))[:400])
    print("workspaces:", json.dumps(me.call("workspaces"))[:300])
    # the .scaffolded shortcut around onboarding (recorded as a shortcut in the report)
    print("scaffold:", json.dumps(me.call(
        "workspace_write", path=".scaffolded",
        content=json.dumps({"ready": True, "at": time.time(), "by": "adoption-sim"})))[:300])

    start = time.time() + 30 * 86400            # far future: await_start parks, no bot ever
    refs = {"organizer": EMAIL, "url": "https://meet.google.com/sim-probe-aaa",
            "start": start, "ics_uid": f"sim-probe-{int(time.time())}",
            "title": "Payments platform weekly", "group": None}
    print("invite:", json.dumps(simlane.emit("invite.received", refs["ics_uid"], refs))[:400])
    time.sleep(45)
    for m in rig.messages_for(EMAIL):
        t = rig.full_touch(m)
        print(f"  MAIL  {t['subject']!r}  links={t['links'][:1]}")
    print("reactions:", json.dumps(me.call("reactions_list"))[:1200])


if __name__ == "__main__":
    main()
