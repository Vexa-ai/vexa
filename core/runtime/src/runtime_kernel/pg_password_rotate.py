"""``python -m runtime_kernel.pg_password_rotate`` — the chart's postgres-password hook
(deploy/helm/charts/vexa/templates/job-postgres-password.yaml), run in the runtime image under the
hook's own ServiceAccount: it moves the bundled database's role off a published password.

It speaks to the API server the way the K8sBackend does (``k8s_backend._api``: HTTPS, the Pod's
ServiceAccount token and CA), and runs ``psql`` inside the postgres Pod through the exec subresource
over a WebSocket (``v4.channel.k8s.io``). The new password only ever travels in a request body or on
the exec's stdin, never in a URL or a command line, and is never printed.

Every step can run again: a failure at any point leaves POSTGRES_PASSWORD_NEXT in the Secret, so the
next upgrade renders the hook again and it finishes with the same value.
  1. mint the new password once and record it as POSTGRES_PASSWORD_NEXT;
  2. set it on the database role, over the postgres Pod's local socket (no old password needed);
  3. make it the Secret's POSTGRES_PASSWORD and restart the Deployments that read it;
  4. remove POSTGRES_PASSWORD_NEXT.

Env: NS, SECRET, POD (the postgres Pod), CONSUMERS (Deployment names, space-separated)."""
from __future__ import annotations

import base64
import json
import os
import secrets
import ssl
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlencode

from . import k8s_backend
from .k8s_backend import ApiError

NEXT, CURRENT = "POSTGRES_PASSWORD_NEXT", "POSTGRES_PASSWORD"
EXEC_TIMEOUT_S = 60.0
TRIES, PAUSE_S = 60, 5.0


def _ns_path(ns: str, rest: str, group: str = "api/v1") -> str:
    return f"/{group}/namespaces/{quote(ns, safe='')}/{rest}"


def _check(method: str, path: str, status: int, obj: dict, ok=(200, 201)) -> dict:
    if status not in ok:
        raise ApiError(method, path, status, obj)
    return obj


def read_field(ns: str, secret: str, key: str) -> str:
    path = _ns_path(ns, f"secrets/{quote(secret, safe='')}")
    obj = _check("GET", path, *k8s_backend._api("GET", path))
    raw = (obj.get("data") or {}).get(key)
    return base64.b64decode(raw).decode() if raw else ""


def set_field(ns: str, secret: str, key: str, value: str) -> None:
    path = _ns_path(ns, f"secrets/{quote(secret, safe='')}")
    body = {"data": {key: base64.b64encode(value.encode()).decode()}}
    _check("PATCH", path, *k8s_backend._api("PATCH", path, body=body, content_type="application/merge-patch+json"))


def remove_field(ns: str, secret: str, key: str) -> None:
    path = _ns_path(ns, f"secrets/{quote(secret, safe='')}")
    patch = [{"op": "remove", "path": f"/data/{key}"}]
    _check("PATCH", path, *k8s_backend._api("PATCH", path, body=patch, content_type="application/json-patch+json"))


def restart(ns: str, deployment: str, now: Optional[str] = None) -> None:
    """What `kubectl rollout restart` does: stamp the Pod template, so the Deployment rolls."""
    path = _ns_path(ns, f"deployments/{quote(deployment, safe='')}", group="apis/apps/v1")
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}}}}
    _check("PATCH", path, *k8s_backend._api("PATCH", path, body=body, content_type="application/merge-patch+json"))


def _connect(url: str, token: str, cafile: str):
    from websockets.sync.client import connect          # in uv.lock (runtime dependency)

    # proxy=None: websockets >= 15 reads HTTPS_PROXY from the environment by default; the exec goes
    # straight to the API server, as every other call does (k8s_backend._api: trust_env=False).
    return connect(url, ssl=ssl.create_default_context(cafile=cafile), subprotocols=["v4.channel.k8s.io"],
                   additional_headers={"Authorization": f"Bearer {token}"}, open_timeout=30, close_timeout=5,
                   proxy=None)


def exec_stdin(ns: str, pod: str, container: str, command: list[str], stdin: bytes,
               timeout: float = EXEC_TIMEOUT_S) -> int:
    """Run ``command`` in the container with ``stdin`` as its input; return its exit code. The
    command's stderr is shown only as its ERROR lines (psql echoes the failing statement after them,
    and the statement carries the password)."""
    params = [("container", container), ("stdin", "true"), ("stdout", "true"), ("stderr", "true")]
    params += [("command", c) for c in command]
    url = k8s_backend._api_base().replace("https://", "wss://", 1) + _ns_path(ns, f"pods/{quote(pod, safe='')}/exec") \
        + "?" + urlencode(params)
    with open(os.path.join(k8s_backend.SA_DIR, "token")) as f:
        token = f.read().strip()
    deadline = time.monotonic() + timeout
    with _connect(url, token, os.path.join(k8s_backend.SA_DIR, "ca.crt")) as ws:
        ws.send(b"\x00" + stdin)                         # channel 0: stdin
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"exec in {pod}: no exit status within {int(timeout)}s")
            msg = ws.recv(timeout=left)
            data = msg.encode() if isinstance(msg, str) else msg
            if not data:
                continue
            channel, payload = data[0], data[1:]
            if channel == 2:                             # stderr: its ERROR lines only
                for line in payload.decode(errors="replace").splitlines():
                    if line.startswith(("ERROR:", "FATAL:", "psql:")) and "PASSWORD" not in line.upper():
                        print(line, file=sys.stderr, flush=True)
            elif channel == 3:                           # the exec's status
                status = json.loads(payload or b"{}")
                if status.get("status") == "Success":
                    return 0
                for cause in (status.get("details") or {}).get("causes") or []:
                    if cause.get("reason") == "ExitCode":
                        return int(cause.get("message") or 1)
                print(f"exec in {pod}: {status.get('message') or status}", file=sys.stderr, flush=True)
                return 1


def _sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _sql_ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def rotate(ns: str, secret: str, pod: str, consumers: list[str], *, tries: int = TRIES,
           pause_s: float = PAUSE_S) -> None:
    nxt = read_field(ns, secret, NEXT)
    if not nxt:
        nxt = secrets.token_hex(32)
        set_field(ns, secret, NEXT, nxt)
    user, db = read_field(ns, secret, "POSTGRES_USER"), read_field(ns, secret, "POSTGRES_DB")
    # psql reads the statement, then \q, from stdin: it exits without needing the stream closed.
    sql = f"ALTER ROLE {_sql_ident(user)} PASSWORD {_sql_literal(nxt)};\n\\q\n".encode()
    for attempt in range(1, tries + 1):
        try:
            code = exec_stdin(ns, pod, "postgres", ["psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"], sql)
        except Exception as exc:  # noqa: BLE001 — the database may still be starting
            print(f"exec in {pod}: {exc}", file=sys.stderr, flush=True)
            code = -1
        if code == 0:
            break
        if attempt == tries:
            raise RuntimeError("the database did not take the new password")
        time.sleep(pause_s)
    set_field(ns, secret, CURRENT, nxt)
    for d in consumers:
        restart(ns, d)
    remove_field(ns, secret, NEXT)


def main() -> int:
    env = os.environ
    rotate(env["NS"], env["SECRET"], env["POD"], (env.get("CONSUMERS") or "").split())
    print("the database role was moved off a published password; its workloads are restarting", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
