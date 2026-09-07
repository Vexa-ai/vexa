"""Prove the capability chain on the REAL services, with no seeding involved.

Owner creates an addressable meeting -> mints a RESTRICTED share for one attendee -> a brand-new
person (no account until now) redeems it through the gateway -> the meeting is visible to them.
Also proves the negative: a DIFFERENT new person holding the same token gets nothing, because the
grant is bound to the first one's verified email.
"""
import json
import sys
import urllib.error
import urllib.request

import rig
from probe import login

GW = "http://127.0.0.1:18456"
ADMIN = "http://127.0.0.1:18457"
OWNER = "sim-cap-owner@rehearsal.test"
GUEST = "sim-cap-guest@rehearsal.test"
INTRUDER = "sim-cap-intruder@rehearsal.test"


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


def admin_key():
    import subprocess
    out = subprocess.run(["docker", "inspect", "vexa-dogfood-admin-api-1", "--format",
                          "{{range .Config.Env}}{{println .}}{{end}}"],
                         capture_output=True, text=True).stdout
    return next((l.split("=", 1)[1].strip() for l in out.splitlines()
                 if l.startswith("ADMIN_API_TOKEN=")), "")


def key_for(uid):
    st, t = api("POST", f"{ADMIN}/admin/users/{uid}/tokens",
                {"X-Admin-API-Key": admin_key()}, {"scopes": ["bot", "browser", "tx"]})
    return (t or {}).get("token") if isinstance(t, dict) else None


def visible(uid):
    st, b = api("GET", f"{GW}/meetings", {"X-API-Key": key_for(uid)})
    rows = b.get("meetings", []) if isinstance(b, dict) else (b if isinstance(b, list) else [])
    return [r.get("id") for r in rows if isinstance(r, dict)]


owner_uid, _ = login(OWNER)
ok = key_for(owner_uid)
native = f"simcap{int(__import__('time').time())}"
st, m = api("POST", f"{GW}/meetings", {"X-API-Key": ok},
            {"title": "Show B Lighting dailies", "meeting_url": f"https://meet.jit.si/{native}"})
print(f"1. owner uid={owner_uid} created meeting -> {st} id={m.get('id')} "
      f"platform={m.get('platform')} native={m.get('native_meeting_id')}")
mid = m.get("id")
platform = m.get("platform")

st, minted = api("POST", f"{GW}/meetings/{platform}/{native}/share", {"X-API-Key": ok},
                 {"mode": "restricted", "allowed_emails": [GUEST], "expires_in_sec": 2592000})
print(f"2. mint restricted share for {GUEST} -> {st} token={'yes' if minted.get('token') else minted}")
token = minted.get("token")
if not token:
    sys.exit("mint failed")

guest_uid, _ = login(GUEST)
print(f"3. attendee signs in for the FIRST time -> uid={guest_uid}")
print(f"   visible BEFORE redeem: {visible(guest_uid)}")
st, r = api("POST", f"{GW}/transcripts/share/accept", {"X-API-Key": key_for(guest_uid)},
            {"token": token})
print(f"4. redeem through the gateway -> {st} {json.dumps(r)[:120]}")
vis = visible(guest_uid)
print(f"   visible AFTER redeem : {vis}   <-- meeting {mid} belongs to uid {owner_uid}")

intruder_uid, _ = login(INTRUDER)
st, r2 = api("POST", f"{GW}/transcripts/share/accept", {"X-API-Key": key_for(intruder_uid)},
             {"token": token})
print(f"5. a DIFFERENT person redeems the SAME token -> {st} {json.dumps(r2)[:120]}")
print(f"   their visible meetings: {visible(intruder_uid)}  <-- must NOT contain {mid}")

print("\nRESULT:", "PASS" if (mid in vis and mid not in visible(intruder_uid)) else "FAIL")
