"""The only way out of this process.

Four doors — gateway, agent-api, admin-api, flows-api — plus the mail double, and nothing else.
Every tool in ``tools/`` reaches a service through one of these functions; ``tests/test_thin_forward.py``
walks the AST of every tool module and fails on any other reach (a subprocess, a database driver, a
``sys.path`` mutation, a write outside ``~/.vexa``), and on any tool that touches more than one of
these four doors.

IDENTITY TRAVELS, CREDENTIALS DO NOT. agent-api takes the caller's subject as ``X-User-Id``; the
gateway takes a per-user API key this module mints ONCE and remembers; admin-api and flows-api take
the deployment's own operator credential. No tool builds a header of its own.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config


def http(method: str, url: str, headers: dict | None = None, body=None, timeout: int = 40):
    """One request → ``(status, parsed-or-text)``. Never raises; a transport failure is status 0.

    A tool that cannot tell "the service said no" from "I could not reach the service" tells a
    person the wrong thing, so both come back as values and neither comes back as an exception.
    """
    h = {"content-type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def admin_key() -> str:
    """admin-api's operator token — A DEPLOYMENT VALUE.

    The rig read this by running ``docker inspect vexa-dogfood-admin-api-1`` and splitting the
    container's environment on ``ADMIN_API_TOKEN=``: a docker socket, a hardcoded container name and
    a subprocess, on a path several tools take more than once. It is a credential and it arrives
    like one (seam inventory B6.2)."""
    return config.ADMIN_API_TOKEN


def admin_headers() -> dict:
    return {"X-Admin-API-Key": admin_key()}


def flows_headers() -> dict:
    return {"X-Flows-Admin-Key": config.FLOWS_API_KEY}


def agent(method: str, path: str, uid: str = "", body=None, timeout: int = 40,
          extra: dict | None = None):
    """agent-api, as the caller. ``path`` starts with ``/``."""
    h = {"X-User-Id": str(uid)} if uid else {}
    if extra:
        h.update(extra)
    return http(method, f"{config.AGENT_API}{path}", h, body, timeout)


def admin(method: str, path: str, body=None, timeout: int = 40):
    """admin-api, as the deployment's operator."""
    return http(method, f"{config.ADMIN_API}{path}", admin_headers(), body, timeout)


def flows(method: str, path: str, body=None, timeout: int = 40):
    """flows-api, as the deployment's operator."""
    return http(method, f"{config.FLOWS_API}{path}", flows_headers(), body, timeout)


def mail(method: str, path: str, timeout: int = 40):
    """The mail double. Not a service — a dev-lane inbox the flows engine sends into."""
    return http(method, f"{config.MAILPIT}{path}", None, None, timeout)


# ── the gateway, and the one key per person it wants ─────────────────────────────────────────
_USER_KEYS: dict = {}


def _user_keys_disk() -> dict:
    try:
        return json.loads(config.USER_KEYS_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def user_key(uid: str, fresh: bool = False) -> str:
    """This person's gateway key — MINTED ONCE, then remembered.

    Every call used to POST a new one. Ten call sites answering one question left nine keys behind,
    66 in total for a single account, and every one of them stays valid forever: a credential leak
    that grows with use. The admin API will not read a key's value back, so the only way to reuse
    one is to remember it — in process, and under ``~/.vexa`` so a restart is not a fresh minting
    spree. Callers go through :func:`gw`, which re-mints once if the remembered key was revoked
    underneath us.
    """
    uid = str(uid)
    if not fresh:
        k = _USER_KEYS.get(uid)
        if not k:
            k = _user_keys_disk().get(uid)
            if k:
                _USER_KEYS[uid] = k
        if k:
            return k
    st, tok = admin("POST", f"/admin/users/{uid}/tokens", {"scopes": ["bot", "browser", "tx"]})
    key = (tok or {}).get("token") or (tok or {}).get("key") or "" if isinstance(tok, dict) else ""
    if key:
        _USER_KEYS[uid] = key
        try:
            d = _user_keys_disk()
            d[uid] = key
            config.USER_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            config.USER_KEYS_FILE.write_text(json.dumps(d, indent=1))
        except Exception:  # noqa: BLE001
            pass
    return key


def gw(uid: str, method: str, path: str, body=None, timeout: int = 40):
    """The single door to the gateway. Retries once on a revoked key, never mints speculatively."""
    st, r = http(method, f"{config.GATEWAY}{path}", {"X-API-Key": user_key(uid)}, body, timeout)
    if st in (401, 403):
        st, r = http(method, f"{config.GATEWAY}{path}",
                     {"X-API-Key": user_key(uid, fresh=True)}, body, timeout)
    return st, r


def q(**params) -> str:
    """A query string from the non-empty parameters, or ``""``. Saves every call site the same
    four-line dance and keeps a ``None`` out of a URL."""
    kept = {k: v for k, v in params.items() if v not in ("", None)}
    return ("?" + urllib.parse.urlencode(kept)) if kept else ""
