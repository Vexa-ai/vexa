"""Phase 3 — what happens AFTER the click.

The touch does not end at the click: the button opens a chat composed by the `?ask=` preset the
link carries, and the person then talks to the REAL agent. Haiku on both sides — the persona is
simulated, the agent is the product as deployed on this stack. Bounded at 3 persona turns.

The attendee has no account until they click, by design (no session is created before anyone
presses the button), so the identity is provisioned HERE, at click time, exactly as the magic
link does it.

Scored on what the preset rule actually promises: the opening must TELL from the workspace and
then ask ONE question. Turns-to-value, asked actions, and abandonment reasons feed retention
directly — a chat that gave nothing makes the next mail ignorable, by the persona's own history.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

import judge
import sample

RUN = os.environ.get("SIM_RUN_DIR", os.path.expanduser("~/sim-runs/r1"))
ADMIN = os.environ.get("SIM_ADMIN_API", "http://127.0.0.1:18457")


def admin_key() -> str:
    import subprocess
    out = subprocess.run(
        ["docker", "inspect", "vexa-dogfood-admin-api-1", "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("ADMIN_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def ensure_uid(email: str) -> str:
    key = admin_key()
    for method, url, body in (("GET", f"{ADMIN}/admin/users/email/{email}", None),
                              ("POST", f"{ADMIN}/admin/users",
                               {"email": email, "name": email.split("@")[0].title()})):
        req = urllib.request.Request(
            url, method=method,
            data=json.dumps(body).encode() if body is not None else None)
        req.add_header("content-type", "application/json")
        req.add_header("X-Admin-API-Key", key)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return str(json.load(r)["id"])
        except Exception:  # noqa: BLE001
            continue
    return ""


# The preset the minutes-review link composes. This is the OPENING the person meets; if the
# product's real preset text changes, this changes with it — it is not our words.
OPENING_PROMPT = (
    "[minutes-review] The person just opened this chat from the follow-up email about meeting "
    "{mid}. Open by TELLING them what this meeting holds for them from the workspace — not by "
    "asking what they want. Then ask exactly ONE question.")


def main():
    cases = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(
        open(f"{RUN}/converse-cases.json"))
    out = []
    for c in cases:
        email, persona, mid = c["email"], c["persona"], c["meeting_id"]
        uid = ensure_uid(email)
        if not uid:
            print(f"skip {email}: no uid")
            continue
        role, dept = sample.ROLE_FOR.get(persona, ("artist", "Show A"))
        who = sample.FakePerson(persona, role, dept, c.get("name", "Sam Okafor"))
        print(f"-> {persona:28s} {email}  uid={uid}", flush=True)
        r = sample.converse(who, uid, f"meet-{mid}", OPENING_PROMPT.format(mid=mid))
        r["email"], r["uid"], r["meeting_id"] = email, uid, mid
        out.append(r)
        json.dump(out, open(f"{RUN}/conversations.json", "w"), indent=1)

    ok = [c for c in out if c["got_value"]]
    told = [c for c in out if c["opened_by_telling"]]
    print("\n=== INTERACTION ===")
    print(f"conversations       : {len(out)}")
    print(f"opened by TELLING   : {len(told)}/{len(out)}")
    print(f"reached value       : {len(ok)}/{len(out)}")
    if ok:
        print("median turns to value:",
              sorted(c["turns_to_value"] for c in ok)[len(ok) // 2])
    print("asked actions       :",
          [a for c in out for a in c["asked_actions"]] or "none")
    for c in out:
        if c["abandoned"] and c["abandon_why"]:
            print(f"  abandoned ({c['persona']}): {c['abandon_why'][:150]}")


if __name__ == "__main__":
    main()
