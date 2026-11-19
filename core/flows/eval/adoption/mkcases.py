"""Build funnel cases: sign each attendee in (the account is created by the click), redeem their
token, and pair them with the mail text the product actually sent."""
import json
import os
import re
import sys
import urllib.request

import rig
from probe import login

GW = "http://127.0.0.1:18456"
ADMIN = "http://127.0.0.1:18457"
ARM = sys.argv[1]                      # "before" | "after"
TAG = sys.argv[2]                      # mailbox tag, e.g. fin | inv
MID = sys.argv[3]
PERSONAS = ["coordinator_under_pressure", "production_manager", "supervisor", "artist"]
BOXES = ["eng1", "eng2", "sup1"]


def api(method, url, headers, body=None, timeout=60):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in {"content-type": "application/json", **headers}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def admin_key():
    import subprocess
    out = subprocess.run(["docker", "inspect", "vexa-dogfood-admin-api-1", "--format",
                          "{{range .Config.Env}}{{println .}}{{end}}"],
                         capture_output=True, text=True).stdout
    return next((l.split("=", 1)[1].strip() for l in out.splitlines()
                 if l.startswith("ADMIN_API_TOKEN=")), "")


ASK = ("[minutes-review] The person just opened this chat from the follow-up email about meeting "
       "{mid}. Open by TELLING them what this meeting holds for them from the workspace — not by "
       "asking what they want. Then ask exactly ONE question.")

# the AFTER arm gets the preset body the product would actually compose
try:
    import subprocess
    body = subprocess.run(["docker", "exec", "vexa-dogfood-agent-api-1", "cat",
                           f"/workspaces/_global/asks/minutes-review-invite.md"],
                          capture_output=True, text=True).stdout
    body = re.sub(r"^---\n.*?\n---\n", "", body, flags=re.S)
except Exception:  # noqa: BLE001
    body = ""

cases = []
for i, persona in enumerate(PERSONAS):
    box = BOXES[i % len(BOXES)]
    email = f"sim-{TAG}-{box}@rehearsal.test"
    mail = ""
    for m in rig.messages_for(email):
        if "what it means for you" in (m.get("Subject") or ""):
            mail = rig.full_touch(m)["text"]
            break
    if not mail:
        print(f"  no mail for {email}")
        continue
    uid, tok = login(email)
    tsh = re.search(r"[?&]tshare=([^&\s]+)", mail)
    if tsh:
        st, t = api("POST", f"{ADMIN}/admin/users/{uid}/tokens",
                    {"X-Admin-API-Key": admin_key()}, {"scopes": ["bot", "browser", "tx"]})
        k = (t or {}).get("token") if isinstance(t, dict) else None
        if k:
            api("POST", f"{GW}/transcripts/share/accept", {"X-API-Key": k},
                {"token": tsh.group(1)})
    prompt = (body.replace("{{meeting}}", MID) if (ARM == "after" and body)
              else ASK.format(mid=MID))
    cases.append({"email": email, "persona": persona, "meeting_id": MID, "uid": uid,
                  "name": "Sam Okafor", "mail_text": mail, "ask_prompt": prompt})
    print(f"  {persona:28s} {email} uid={uid}")

out = os.path.expanduser(f"~/sim-runs/r4/cases-{ARM}.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(cases, open(out, "w"), indent=1)
print(f"{len(cases)} cases -> {out}")
