"""Shared fixtures for the gate:compose stack-readiness proof.

A single session-scoped `stack` fixture brings the REAL v0.12 compose stack up
(`docker compose up -d --build`), waits for every service `healthy` with a bounded poll, yields a
`Stack` handle (URLs + exec helpers), and tears it ALL down (`down -v`) in a guaranteed finally — so
a green run leaves no containers/volumes behind.

Everything is poll-with-bounded-timeout: never sleep-and-hope. `requires_docker` self-skips the whole
module when docker is absent (the green-or-skip contract the gate relies on).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

COMPOSE_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = COMPOSE_DIR / "docker-compose.yml"
PROJECT = "vexa-compose-gate"  # an isolated project name so we never collide with a dev `up`

# The four built services + the host ports they publish (mirrors docker-compose.yml defaults).
GATEWAY_URL = "http://127.0.0.1:18056"
ADMIN_API_URL = "http://127.0.0.1:18057"
MEETING_API_URL = "http://127.0.0.1:18080"
RUNTIME_URL = "http://127.0.0.1:18090"

# Env the stack boots with — pinned so the test knows the secrets it must present.
ADMIN_TOKEN = "gate-admin-token"
INTERNAL_API_SECRET = "gate-internal-secret"
MINIO_BUCKET = "vexa"

SERVICES = ["redis", "postgres", "minio", "admin-api", "runtime", "meeting-api", "gateway"]
HEALTHCHECKED = ["redis", "postgres", "minio", "admin-api", "runtime", "meeting-api", "gateway"]


def docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=True)
        return True
    except Exception:
        return False


requires_docker = pytest.mark.skipif(not docker_available(), reason="docker not available")


def _compose(*args: str, env: dict | None = None, check: bool = True, timeout: int = 1200) -> subprocess.CompletedProcess:
    base = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE)]
    full_env = {**os.environ, **_stack_env(), **(env or {})}
    return subprocess.run(base + list(args), capture_output=True, text=True, env=full_env,
                          check=check, timeout=timeout)


def _stack_env() -> dict:
    return {
        "IMAGE_TAG": "dev",
        "ADMIN_TOKEN": ADMIN_TOKEN,
        "INTERNAL_API_SECRET": INTERNAL_API_SECRET,
        "MINIO_BUCKET": MINIO_BUCKET,
        "BROWSER_IMAGE": os.getenv("BROWSER_IMAGE", "vexaai/vexa-bot:dev"),
        # Docker-Desktop / Linux root socket → group 0 is fine for the mounted socket.
        "DOCKER_GID": os.getenv("DOCKER_GID", "0"),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "info"),
    }


def http(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
         timeout: float = 10.0):
    """A tiny urllib client → (status_code, parsed_json_or_text). Never raises on 4xx/5xx."""
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, _parse(raw)
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())
    except Exception as e:  # connection refused while a service is still booting → caller polls
        return 0, str(e)


def _parse(raw: bytes):
    try:
        return json.loads(raw.decode())
    except Exception:
        return raw.decode(errors="replace")


def post_json(url: str, payload: dict, *, headers: dict | None = None, timeout: float = 15.0):
    h = {"Content-Type": "application/json", **(headers or {})}
    return http("POST", url, headers=h, body=json.dumps(payload).encode(), timeout=timeout)


def _service_health(project: str) -> dict[str, str]:
    """Map each compose service → its container Health (or running State when no healthcheck)."""
    out = _compose("ps", "--format", "json", check=False).stdout.strip()
    states: dict[str, str] = {}
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        svc = row.get("Service")
        if not svc:
            continue
        states[svc] = row.get("Health") or row.get("State") or "unknown"
    return states


@dataclass
class Stack:
    gateway: str = GATEWAY_URL
    admin_api: str = ADMIN_API_URL
    meeting_api: str = MEETING_API_URL
    runtime: str = RUNTIME_URL
    admin_token: str = ADMIN_TOKEN
    internal_secret: str = INTERNAL_API_SECRET
    bucket: str = MINIO_BUCKET

    # ---- exec helpers (the docker CLI is our DB + S3 probe; no extra client deps) ----
    def exec(self, service: str, *cmd: str, check: bool = True) -> str:
        r = _compose("exec", "-T", service, *cmd, check=check, timeout=120)
        return (r.stdout or "").strip()

    def psql(self, sql: str) -> str:
        """Run SQL, returning ONLY the result rows (the psql command-status tag — `INSERT 0 1`,
        `UPDATE 1`, … — is stripped so a `RETURNING id` yields a clean scalar)."""
        raw = self.exec("postgres", "psql", "-U", "postgres", "-d", "vexa", "-tAq", "-c", sql)
        tag = ("INSERT ", "UPDATE ", "DELETE ", "SELECT ", "BEGIN", "COMMIT")
        rows = [ln for ln in raw.splitlines() if ln and not ln.startswith(tag)]
        return "\n".join(rows).strip()

    def minio_ls(self, prefix: str) -> list[str]:
        """List minio object keys under a prefix via the mc client baked into the minio image."""
        # alias is set lazily; ignore the error if it already exists.
        self.exec("minio", "mc", "alias", "set", "local", "http://localhost:9000",
                  "vexa-access-key", "vexa-secret-key", check=False)
        out = self.exec("minio", "mc", "ls", "--recursive", f"local/{self.bucket}/{prefix}", check=False)
        keys = []
        for line in out.splitlines():
            parts = line.split()
            if parts:
                keys.append(parts[-1])
        return keys

    def redis_cli(self, *args: str) -> str:
        return self.exec("redis", "redis-cli", *args, check=False)

    def logs(self, service: str, *, tail: int = 400) -> str:
        return _compose("logs", "--no-color", "--tail", str(tail), service, check=False).stdout

    def redis_host_url(self) -> str:
        port = self.redis_host_port
        return f"redis://127.0.0.1:{port}/0"

    redis_host_port: int = 0


def _wait_healthy(deadline: float, poll: float = 3.0) -> dict[str, str]:
    last: dict[str, str] = {}
    while time.time() < deadline:
        last = _service_health(PROJECT)
        # minio-init is a one-shot — it exits 0 and disappears; don't require it here.
        relevant = {s: last.get(s, "missing") for s in HEALTHCHECKED}
        if all(v == "healthy" for v in relevant.values()):
            return relevant
        time.sleep(poll)
    raise TimeoutError(f"stack did not become healthy in time: {last}")


@pytest.fixture(scope="session")
def stack():
    if not docker_available():
        pytest.skip("docker not available")
    if not COMPOSE_FILE.exists():
        pytest.skip("compose file missing (green-on-empty)")

    # Clean any prior gate run, then bring it up + build (P4 images are cached → fast on a warm host).
    _compose("down", "-v", "--remove-orphans", check=False)
    build = os.getenv("COMPOSE_NO_BUILD") != "1"
    up_args = ["up", "-d", "--remove-orphans"] + (["--build"] if build else [])
    _compose(*up_args, timeout=1800)

    s = Stack()
    # Discover the published redis host port (it isn't published by default → use an ephemeral one).
    try:
        s.redis_host_port = int(_compose("port", "redis", "6379", check=False).stdout.strip().rsplit(":", 1)[-1])
    except Exception:
        s.redis_host_port = 0

    try:
        states = _wait_healthy(time.time() + 240)
        # Cross-check: the gateway's /health actually answers over the published host port.
        _poll_http(f"{s.gateway}/health", deadline=time.time() + 60)
        print(f"\n[gate:compose] stack healthy: {states}")
        yield s
    finally:
        _cleanup(s)


def _cleanup(s: Stack) -> None:
    # Remove any bot containers the runtime spawned on the HOST daemon (default-bridge, outside the
    # compose project) so `down -v` leaves nothing behind.
    try:
        names = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^vexa-mtg-"],
            capture_output=True, text=True, timeout=30,
        ).stdout.split()
        if names:
            subprocess.run(["docker", "rm", "-f", *names], capture_output=True, timeout=60)
    except Exception:
        pass
    _compose("down", "-v", "--remove-orphans", check=False, timeout=300)


def _poll_http(url: str, *, deadline: float, want: int = 200, poll: float = 2.0):
    while time.time() < deadline:
        code, _ = http("GET", url, timeout=5.0)
        if code == want:
            return
        time.sleep(poll)
    raise TimeoutError(f"{url} never returned {want}")
