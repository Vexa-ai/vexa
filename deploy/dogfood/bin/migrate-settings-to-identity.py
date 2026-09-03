#!/usr/bin/env python3
"""ONE-SHOT: move every `.settings.json` off the agent workspaces and into identity.

`timezone` and the four mail preferences were facts about a PERSON kept in a file in a workspace in
the AGENT domain. They live in identity now (`admin_api.app.person_settings`), which is the only
domain flows and the MCP may depend on — so a deployment without the agent domain still has people
with a clock and a way to stop the mail.

    python3 migrate-settings-to-identity.py --dry-run          # say what it would do
    python3 migrate-settings-to-identity.py                    # do it

It is IDEMPOTENT: the import route keeps any key the person has already set through the new door,
so re-running it cannot undo somebody's choice. `bot_name` is DROPPED — it is a fact about the bot,
not the person, and this move deliberately did not touch it.

Reads the workspaces through agent-api (the domain that owns those files) and writes through
admin-api's internal tier. Both are read from the environment; nothing is hardcoded and no container
is named.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ADMIN_API = (os.environ.get("VEXA_ADMIN_API_URL") or "http://localhost:18457").rstrip("/")
AGENT_API = (os.environ.get("VEXA_AGENT_API_URL") or "http://localhost:18500").rstrip("/")
ADMIN_TOKEN = os.environ.get("VEXA_ADMIN_API_TOKEN") or os.environ.get("ADMIN_API_TOKEN") or ""
INTERNAL_SECRET = os.environ.get("INTERNAL_API_SECRET") or ""


def _req(method: str, url: str, headers: dict, body=None, timeout: int = 20):
    req = urllib.request.Request(
        url, method=method, headers={"content-type": "application/json", **headers},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — internal service URL
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def users() -> list:
    st, body = _req("GET", f"{ADMIN_API}/admin/users?limit=1000",
                    {"X-Admin-API-Key": ADMIN_TOKEN})
    if st != 200 or not isinstance(body, list):
        print(f"could not list users: admin-api answered {st}: {str(body)[:200]}", file=sys.stderr)
        return []
    return body


def legacy_settings(uid) -> dict | None:
    """One person's `.settings.json`, through the domain that owns the file. None = nothing to do."""
    st, body = _req("GET", f"{AGENT_API}/api/workspace/file?path=.settings.json",
                    {"X-User-Id": str(uid)})
    if st != 200 or not isinstance(body, dict):
        return None
    try:
        parsed = json.loads(body.get("content") or "")
    except Exception:  # noqa: BLE001
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()
    if not ADMIN_TOKEN or (not args.dry_run and not INTERNAL_SECRET):
        print("set VEXA_ADMIN_API_TOKEN, and INTERNAL_API_SECRET to write", file=sys.stderr)
        return 2

    moved = skipped = failed = 0
    for u in users():
        uid, email = u.get("id"), u.get("email") or "?"
        legacy = legacy_settings(uid)
        if not legacy:
            skipped += 1
            continue
        if args.dry_run:
            print(f"  would import {uid} {email}: {sorted(legacy)}")
            moved += 1
            continue
        st, body = _req("POST", f"{ADMIN_API}/internal/users/{uid}/settings/import",
                        {"X-Internal-Secret": INTERNAL_SECRET}, legacy)
        if st != 200 or not isinstance(body, dict):
            print(f"  FAILED {uid} {email}: {st} {str(body)[:160]}", file=sys.stderr)
            failed += 1
            continue
        print(f"  {uid} {email}: imported={sorted(body['imported'])} "
              f"kept={body['kept']} dropped={body['dropped']}")
        moved += 1
    verb = "would move" if args.dry_run else "moved"
    print(f"\n{verb} {moved}, nothing to do for {skipped}, failed {failed}")
    # The legacy files are LEFT IN PLACE on purpose: this is reversible until somebody deletes them,
    # and `bot_name` — the one key that did not move — is still read from them.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
