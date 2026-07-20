"""gate:compose-chaos (A:V3) — the control plane RECOVERS from injected dependency faults (P18).

The MOCK_BOT lane proves the happy + edge paths; this proves RESILIENCE: inject a transient fault into a
live dependency mid-run (a redis blip · a meeting-api blip — via `docker pause`/`unpause`, no extra infra)
and assert the run still reaches a clean terminal — the bot's HTTP lifecycle sink + redis clients
retry/back off, the meeting is never left silently stalled. A transient dependency fault is SURVIVED, not
swallowed (P18). Gated COMPOSE_CHAOS=1 (opt-in; heavier). SoC: still backend ⊥ worker (a mock bot).
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest

from conftest import COMPOSE_FILE, PROJECT, requires_docker
from mock_scenarios_test import _meeting, _spawn, _stop_bot, _wait_meeting
from stack_test import _create_user

pytestmark = requires_docker

CHAOS = os.getenv("COMPOSE_CHAOS") == "1"
chaos_only = pytest.mark.skipif(
    not CHAOS, reason="chaos suite is opt-in (COMPOSE_CHAOS=1 + MOCK_BOT=1 + BROWSER_IMAGE=mock-bot:dev)"
)


def _container_id(service: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), "ps", "-q", service],
        capture_output=True, text=True, timeout=30,
    )
    return (r.stdout or "").strip().splitlines()[0] if r.stdout.strip() else ""


def _blip(service: str, secs: float):
    """Pause a service container for `secs` (a transient fault), then resume it."""
    cid = _container_id(service)
    assert cid, f"could not resolve the {service} container"
    subprocess.run(["docker", "pause", cid], capture_output=True, timeout=30)
    time.sleep(secs)
    subprocess.run(["docker", "unpause", cid], capture_output=True, timeout=30)


# --- #528 helpers: harsher redis faults + the zero-restart evidence -----------------------------
def _inspect(cid: str, fmt: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f", fmt, cid], capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip()


def _restart_state(service: str) -> tuple[int, str]:
    """(RestartCount, StartedAt) for a service container — unchanged across a fault == it self-healed
    WITHOUT a restart, which is the #528 property (a bare client would need a manual restart to heal)."""
    cid = _container_id(service)
    assert cid, f"could not resolve the {service} container"
    rc = _inspect(cid, "{{.RestartCount}}") or "0"
    return int(rc), _inspect(cid, "{{.State.StartedAt}}")


def _redis_network(cid: str) -> str:
    nets = _inspect(cid, "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}").split()
    return nets[0] if nets else ""


def _compose(*args: str):
    subprocess.run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
                   capture_output=True, timeout=60)


def _wait_redis_dependents_healthy(stack, timeout: float = 45):
    from conftest import http
    deadline = time.time() + timeout
    while time.time() < deadline:
        c, _b = http("GET", f"{stack.meeting_api}/health", timeout=5)
        if c == 200:
            return True
        time.sleep(2)
    return False


# Services that hold a Redis client and MUST NOT be restarted to recover from a redis fault (#528).
_REDIS_DEPENDENTS = ("meeting-api", "gateway", "runtime")


@chaos_only
def test_chaos_redis_blip_survived(stack):
    """A redis blip during the active phase → the bot + collector reconnect; the run still reaches terminal."""
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "immediate-stop")  # stays active until told to leave
    m = _wait_meeting(stack, user_id, native_id, statuses={"active", "joining", "awaiting_admission"}, timeout=60)
    assert m, "chaos: bot never registered a live meeting"

    _blip("redis", 3.0)  # transient redis outage mid-run

    # The backend survives: stopping still works (the leave command + the bot's retrying lifecycle sink),
    # and the meeting reaches a clean terminal — not a silent stall.
    _stop_bot(stack, user_id, native_id)
    term = _wait_meeting(stack, user_id, native_id, statuses={"completed", "failed"}, timeout=90)
    assert term and term["status"] in ("completed", "failed"), f"redis blip not survived — meeting stalled: {term}"
    print(f"\n[chaos/redis-blip] 3s redis outage mid-run → meeting still reached {term['status']} (survived)")


@chaos_only
def test_chaos_meeting_api_blip_survived(stack):
    """A meeting-api blip while the bot reports lifecycle → the bot's HTTP sink retries/backs off; on
    recovery the callbacks land and the meeting reaches terminal (no lost lifecycle)."""
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "normal")  # emits joining→active→…→completed callbacks
    # Blip meeting-api right as the bot starts reporting — its lifecycle-http adapter retries/backs off.
    _blip("meeting-api", 3.0)
    # Cross-check the stack itself recovered (health answers again) before asserting the run's outcome.
    from conftest import http
    deadline = time.time() + 30
    while time.time() < deadline:
        c, _b = http("GET", f"{stack.meeting_api}/health", timeout=5)
        if c == 200:
            break
        time.sleep(2)
    term = _wait_meeting(stack, user_id, native_id, statuses={"completed", "failed"}, timeout=120)
    assert term and term["status"] in ("completed", "failed"), f"meeting-api blip not survived — meeting stalled: {term}"
    print(f"\n[chaos/meeting-api-blip] 3s meeting-api outage → callbacks retried → meeting reached {term['status']}")


@chaos_only
def test_chaos_redis_refused_then_restored_no_restart(stack):
    """#528 · B2a — redis STOPPED then STARTED (connection refused, then back) mid-run. Every
    Redis-dependent service must self-heal WITHOUT a restart (bounded socket_timeout + health checks)
    and the meeting must still reach terminal. Base (bare clients): the run stalls and/or a dependent
    needs a manual restart — red on the terminal assert or the RestartCount assert."""
    before = {s: _restart_state(s) for s in _REDIS_DEPENDENTS}
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "immediate-stop")
    assert _wait_meeting(stack, user_id, native_id,
                         statuses={"active", "joining", "awaiting_admission"}, timeout=60), "no live meeting"

    _compose("stop", "redis")     # connection refused for everyone holding a client
    time.sleep(45)
    _compose("start", "redis")
    assert _wait_redis_dependents_healthy(stack), "meeting-api /health never recovered after redis restart"

    _stop_bot(stack, user_id, native_id)
    term = _wait_meeting(stack, user_id, native_id, statuses={"completed", "failed"}, timeout=90)
    assert term and term["status"] in ("completed", "failed"), f"redis stop/start not survived: {term}"
    for s in _REDIS_DEPENDENTS:
        assert _restart_state(s) == before[s], f"{s} was RESTARTED to recover from the redis outage (#528 not met)"
    print(f"\n[chaos/redis-refused] stop→45s→start → meeting reached {term['status']}, zero dependent restarts")


@chaos_only
def test_chaos_redis_silent_drop_no_restart(stack):
    """#528 · B2b — the LOAD-BEARING negative control: redis silently NETWORK-PARTITIONED (no RST, no
    refused — the socket just goes dead), the exact 2026-04-26 shape a bare client never notices. With
    socket_timeout + health checks the dead socket raises and the pool revalidates; the run recovers
    with zero dependent restarts. Base: the client hangs on the dead socket indefinitely → stall."""
    cid = _container_id("redis")
    net = _redis_network(cid)
    assert cid and net, "could not resolve redis container/network"
    before = {s: _restart_state(s) for s in _REDIS_DEPENDENTS}
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "immediate-stop")
    assert _wait_meeting(stack, user_id, native_id,
                         statuses={"active", "joining", "awaiting_admission"}, timeout=60), "no live meeting"

    subprocess.run(["docker", "network", "disconnect", net, cid], capture_output=True, timeout=30)
    time.sleep(60)  # long enough that a bare (no-timeout) client would sit on the dead socket forever
    subprocess.run(["docker", "network", "connect", net, cid], capture_output=True, timeout=30)
    assert _wait_redis_dependents_healthy(stack), "meeting-api /health never recovered after redis reconnect"

    _stop_bot(stack, user_id, native_id)
    term = _wait_meeting(stack, user_id, native_id, statuses={"completed", "failed"}, timeout=90)
    assert term and term["status"] in ("completed", "failed"), f"redis silent-drop not survived: {term}"
    for s in _REDIS_DEPENDENTS:
        assert _restart_state(s) == before[s], f"{s} was RESTARTED to recover from the redis partition (#528 not met)"
    print(f"\n[chaos/redis-silent-drop] disconnect→60s→connect → meeting reached {term['status']}, zero dependent restarts")
