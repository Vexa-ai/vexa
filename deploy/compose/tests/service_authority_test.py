"""L3 product-boundary oracle for service-authority.v1 (#988 A5).

Opt in with SERVICE_AUTHORITY_COMPOSE=1 and the companion Compose overlay.  The
test keeps a real mock bot active for one service minute, observes the persisted
deny/lease, and proves that the real runtime teardown converges exactly once.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from conftest import http, post_json, requires_docker
from stack_test import _create_user


pytestmark = requires_docker
authority_only = pytest.mark.skipif(
    os.getenv("SERVICE_AUTHORITY_COMPOSE") != "1",
    reason=(
        "service-authority Compose oracle is opt-in "
        "(set SERVICE_AUTHORITY_COMPOSE=1 and COMPOSE_EXTRA_FILES)"
    ),
)

REQUEST_KEYS = {
    "user_id",
    "action",
    "request_id",
    "service_identity",
    "service_mode",
    "transcription_provider",
    "lifecycle_contract_version",
    "active_concurrency",
}
CONTINUATION_KEYS = REQUEST_KEYS | {"admitted_at", "boundary_at"}


def _meeting(stack, user_id: int, native_id: str) -> dict | None:
    raw = stack.psql(
        "SELECT json_build_object("
        "'id', id, 'status', status, 'bot_container_id', bot_container_id, "
        "'data', data)::text "
        "FROM meetings "
        f"WHERE user_id={user_id} "
        f"AND platform_specific_id='{native_id}' "
        "ORDER BY id DESC LIMIT 1;"
    )
    return json.loads(raw) if raw else None


def _wait_for(predicate, *, timeout: float, poll: float = 1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(poll)
    raise AssertionError(f"condition not met within {timeout}s; last={last!r}")


def _fixture_observations(stack) -> dict:
    raw = stack.exec(
        "meeting-api",
        "python",
        "-c",
        (
            "import urllib.request;"
            "print(urllib.request.urlopen("
            "'http://authority-fixture:9880/observations'"
            ").read().decode())"
        ),
    )
    return json.loads(raw)


@authority_only
def test_active_service_boundary_stops_once_and_replay_is_inert(stack) -> None:
    user_id = _create_user(stack, max_bots=5)
    native_id = f"authority-{uuid.uuid4().hex[:8]}"
    code, created = post_json(
        f"{stack.meeting_api}/bots",
        {
            "platform": "google_meet",
            "native_meeting_id": native_id,
            "bot_name": "mock:immediate-stop",
            "transcribe_enabled": False,
            "recording_enabled": False,
        },
        headers={"x-user-id": str(user_id), "x-user-limits": "5"},
    )
    assert code == 201, f"authority-backed POST /bots → {code} {created}"

    active = _wait_for(
        lambda: (
            row
            if (row := _meeting(stack, user_id, native_id))
            and row["status"] == "active"
            else None
        ),
        timeout=45,
    )
    authority = active["data"]["service_authority"]
    service_identity = authority["service_identity"]
    admission_decision = authority["decision_id"]
    workload_id = active["bot_container_id"]
    assert authority["mode"] == "enforce"
    assert authority["allow"] is True
    assert authority["reason"] == "compose_fixture_allow"
    assert service_identity.startswith("meeting-session:")
    assert workload_id
    assert http("GET", f"{stack.runtime}/workloads/{workload_id}")[0] == 200

    stopped = _wait_for(
        lambda: (
            row
            if (row := _meeting(stack, user_id, native_id))
            and row["data"]["service_authority"].get(
                "teardown_confirmed",
            )
            is True
            else None
        ),
        timeout=95,
    )
    final_authority = stopped["data"]["service_authority"]
    stop_decision = final_authority["decision_id"]
    boundary = final_authority["last_boundary_at"]
    assert stop_decision != admission_decision
    assert final_authority["allow"] is False
    assert final_authority["reason"] == "compose_fixture_limit"
    assert final_authority["stop_scope"] == "billable_service"
    assert final_authority["service_identity"] == service_identity
    assert stopped["data"]["stop_requested"] is True
    runtime_code, runtime_status = http(
        "GET",
        f"{stack.runtime}/workloads/{workload_id}",
    )
    # runtime.v1 retains an authoritative terminal tombstone; 404 means
    # "untracked", not "successfully destroyed".
    assert runtime_code == 200
    assert runtime_status["state"] == "destroyed"

    observations = _fixture_observations(stack)
    assert observations["signature_failures"] == 0
    assert observations["admit"] == 1
    assert observations["continue"] == 1
    assert observations["service_identities"] == [
        service_identity,
        service_identity,
    ]
    assert set(observations["request_keys"][0]) == REQUEST_KEYS
    assert set(observations["request_keys"][1]) == CONTINUATION_KEYS

    # Wait beyond another sweep tick. A terminal/stopping stop with a confirmed
    # lease is not reconsidered, so neither the provider effect nor teardown is
    # repeated.
    time.sleep(3)
    replay = _meeting(stack, user_id, native_id)
    replay_observations = _fixture_observations(stack)
    assert replay["data"]["service_authority"]["decision_id"] == stop_decision
    assert replay["data"]["service_authority"]["last_boundary_at"] == boundary
    assert replay["data"]["service_authority"]["teardown_confirmed"] is True
    assert replay_observations["admit"] == 1
    assert replay_observations["continue"] == 1

    print(
        "\n[service-authority/A5] "
        f"meeting={stopped['id']} "
        f"service_identity={service_identity} "
        f"decision={stop_decision} "
        f"boundary={boundary} "
        "runtime=destroyed replay=unchanged"
    )
