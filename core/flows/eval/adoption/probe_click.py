"""probe_click — the attendee journey, end to end, as a person with NO account.

Reproduces exactly what happens when somebody who was in a meeting presses the button in the
follow-up mail:

  1. read the REAL mail out of mailpit and take the link the product actually sent
  2. sign in for the first time (the magic-link hop) — this is where the account is created
  3. redeem ?tshare= the way the terminal does on landing (POST /transcripts/share/accept)
  4. confirm the meeting is now VISIBLE to them (it belongs to the organiser)
  5. open the chat on the minutes-review preset and print what it says

The pass condition is step 5: the opening must TELL them about THAT meeting. Before the fix it
was the new-user product pitch, because steps 3 and 4 could not happen.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

import rig
from probe import login

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "sim-shr-eng1@rehearsal.test"
GATEWAY = "http://127.0.0.1:18456"
AGENT_API = "http://127.0.0.1:18500"


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
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)


import subprocess as _sp


def _admin_key():
    out = _sp.run(["docker", "inspect", "vexa-dogfood-admin-api-1", "--format",
                   "{{range .Config.Env}}{{println .}}{{end}}"],
                  capture_output=True, text=True).stdout
    for ln in out.splitlines():
        if ln.startswith("ADMIN_API_TOKEN="):
            return ln.split("=", 1)[1].strip()
    return ""


def key_for(u):
    _st, t = api("POST", f"http://127.0.0.1:18457/admin/users/{u}/tokens",
                 {"X-Admin-API-Key": _admin_key()}, {"scopes": ["bot", "browser", "tx"]})
    return (t or {}).get("token") if isinstance(t, dict) else None


# Visibility is asked of the PRODUCT's own API (the gateway), not of a rig verb. The first
# version of this probe called the rig's meetings_list and reported 0 after a redeem that had
# plainly worked — that verb answers a different question than GET /meetings does, so the harness
# was reporting the harness. Ask the surface the person's client actually uses.
def _visible(u):
    k = key_for(u)
    if not k:
        return []
    _st, b = api("GET", f"{GATEWAY}/meetings", {"X-API-Key": k})
    rows = b.get("meetings", []) if isinstance(b, dict) else (b if isinstance(b, list) else [])
    return [r.get("id") for r in rows if isinstance(r, dict)]


# 1 ── the real mail
mail = None
for m in rig.messages_for(EMAIL):
    if "what it means for you" in (m.get("Subject") or ""):
        mail = rig.full_touch(m)
        break
if not mail:
    raise SystemExit(f"no follow-up mail for {EMAIL}")
link = next((u for u in mail["links"] if "ask=" in u), mail["links"][0] if mail["links"] else "")
print(f"MAIL   : {mail['subject']}")
print(f"LINK   : {link}")
tshare = re.search(r"[?&]tshare=([^&\s]+)", link)
mid = re.search(r"[?&]meeting=(\d+)", link)
print(f"tshare : {'present' if tshare else 'ABSENT — the attendee will see nothing'}")
if not mid:
    raise SystemExit("no meeting id in the link")
meeting_id = mid.group(1)

# 2 ── first sign-in: the account is created HERE, by clicking
uid, tok = login(EMAIL)
print(f"UID    : {uid} (created by the click — this person never onboarded)")
n_before = len(_visible(uid))
print(f"VISIBLE BEFORE REDEEM : {n_before} meeting(s)")

me = rig.MCP(tok)
me.init()

# 3 ── redeem, the way the terminal does on landing.
# Through the GATEWAY with this person's own API key, never agent-api with a hand-set X-User-Id:
# the gateway resolves the VERIFIED email and injects x-user-email itself (it strips a spoofed
# one — gateway/app.py:437 and its test). Restricted mode is checked against that resolved
# address, so this path proves the capability is bound to the attendee and not to whoever holds
# the link.
api_key = key_for(uid)

if tshare and api_key:
    st, body = api("POST", f"{GATEWAY}/transcripts/share/accept",
                   {"X-API-Key": api_key}, {"token": tshare.group(1)})
    print(f"REDEEM : {st} {json.dumps(body)[:200]}")
elif not tshare:
    print("REDEEM : skipped — no token on the link")

# 4 ── visible now?
rows = _visible(uid)
_stm, _one = api("GET", f"{GATEWAY}/meetings/{meeting_id}", {"X-API-Key": api_key})
print(f"VISIBLE AFTER REDEEM  : {len(rows)} meeting(s) -> {rows[:5]}"
      f"   · GET /meetings/{meeting_id} -> {_stm}")

# 5 ── the chat the button opens
prompt = ("[minutes-review] The person just opened this chat from the follow-up email about "
          f"meeting {meeting_id}. Open by TELLING them what this meeting holds for them from the "
          "workspace — not by asking what they want. Then ask exactly ONE question.")
req = urllib.request.Request(f"{AGENT_API}/api/chat", method="POST",
                             data=json.dumps({"prompt": prompt,
                                              "session": f"meet-{meeting_id}"}).encode())
req.add_header("content-type", "application/json")
req.add_header("X-User-Id", str(uid))
reply = ""
try:
    with urllib.request.urlopen(req, timeout=240) as r:
        for raw in r:
            line = raw.decode(errors="replace").strip()
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except Exception:  # noqa: BLE001
                    continue
                if ev.get("type") == "done":
                    reply = ev.get("reply") or reply
except Exception as e:  # noqa: BLE001
    reply = f"(no answer: {type(e).__name__})"

print("\n=== THE CHAT THE BUTTON OPENS ===")
print(reply[:1600])
json.dump({"email": EMAIL, "uid": uid, "link": link, "tshare": bool(tshare),
           "visible_before": n_before, "visible_after": len(rows), "visible_ids": rows, "opening": reply},
          open(f"/home/dima/sim-runs/r1/attendee-click-{EMAIL.split('@')[0]}.json", "w"), indent=1)
