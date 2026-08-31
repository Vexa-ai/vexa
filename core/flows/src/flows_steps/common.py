"""Shared plumbing for production steps: config by env (P14), tiny HTTP, admin/user auth.
STATELESS BY LAW: everything a step needs travels in ctx.refs / ctx.prior — worker restarts
must be invisible (the duplicate-email lesson)."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from typing import Optional

GATEWAY = os.environ.get("VEXA_FLOWS_GATEWAY_URL", "http://localhost:18056")
AGENT_API = os.environ.get("VEXA_FLOWS_AGENT_API_URL", "http://localhost:18100")
ADMIN_API = os.environ.get("VEXA_FLOWS_ADMIN_API_URL", "http://localhost:18057")
ADMIN_KEY = os.environ.get("VEXA_FLOWS_ADMIN_KEY", "changeme")
FIXTURE_TRANSCRIPT = os.environ.get("VEXA_FLOWS_FIXTURE_TRANSCRIPT", "") == "1"   # declared double


def db_url() -> str:
    url = os.environ.get("VEXA_FLOWS_DB_URL")
    if url:
        return url
    pw = subprocess.run(["docker", "exec", "vexa-v012-postgres-1", "sh", "-c", "echo -n $POSTGRES_PASSWORD"],
                        capture_output=True, text=True).stdout.strip()
    return f"postgresql+psycopg://postgres:{pw}@127.0.0.1:5458/flows"


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 20):
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
    except Exception as e:  # noqa: BLE001 — steps turn this into a typed retry
        from flows import StepError
        # The reason column is the only thing anyone reads when a reaction is stuck, so it has to
        # carry the cause. It used to carry `TypeError` alone — the class name of an exception
        # raised while building a header, against a url this line had already truncated to the
        # host. Hours went into finding a deleted user behind that word.
        raise StepError(f"http {method} {url}: {type(e).__name__}: {e}"[:400])


def ensure_platform_user(email: str) -> str:
    code, u = http("GET", f"{ADMIN_API}/admin/users/email/{email}", {"X-Admin-API-Key": ADMIN_KEY})
    if code != 200:
        code, u = http("POST", f"{ADMIN_API}/admin/users", {"X-Admin-API-Key": ADMIN_KEY},
                       {"email": email, "name": email.split("@")[0].title()})
    return str(u["id"])


def user_api_key(uid: str) -> str:
    """This user's gateway key, or a StepError that says why there isn't one.

    Returning None here was the whole bug: the caller put it straight into an X-API-Key header,
    urllib died joining it, and the reaction blamed the gateway for a 404 from the admin API. A
    key that cannot be minted is a fact about the account, and it belongs in the reason.
    """
    st, tok = http("POST", f"{ADMIN_API}/admin/users/{uid}/tokens",
                   {"X-Admin-API-Key": ADMIN_KEY}, {"scopes": ["bot", "browser", "tx"]})
    key = tok.get("token") or tok.get("key") if isinstance(tok, dict) else None
    if not key:
        from flows import StepError
        detail = (tok.get("detail") if isinstance(tok, dict) else str(tok))
        raise StepError(f"no api key for platform user {uid} — admin api said {st}: "
                        f"{str(detail)[:120]}")
    return key


def ws_file(uid: str, path: str, slug: Optional[str] = None) -> Optional[str]:
    q = f"&slug={slug}" if slug else ""
    code, body = http("GET", f"{AGENT_API}/api/workspace/file?path={path}{q}", {"X-User-Id": uid})
    return body.get("content") if code == 200 and isinstance(body, dict) else None


def scaffolded(uid: str, slug: Optional[str] = None) -> bool:
    return ws_file(uid, ".scaffolded", slug) is not None
