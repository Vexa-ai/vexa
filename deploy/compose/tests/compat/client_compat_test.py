"""client-compat · what clients already deployed against 0.12 still need from a 0.13.4 stack.

The `client-compat` check (Task #1919, fix #1). It runs inside the `validate-v010-compat` leg of
release-validate.yml (`bin/stack-test compat` collects all of compat/), and on its own with
`V010_COMPAT=1 MOCK_BOT=1 bin/stack-test compat/client_compat_test.py`. It passes on the candidate
and fails on v0.13.4-rc.1 (negative control), which refused both of the first two below.

  1. `/ws?api_key=<key>` (the hosted dashboard live view, ASWF DNA, clients built from the 0.12
     docs) opens, answers a ping and subscribes to the caller's own meeting. Deprecated; 0.13.6
     turns it off, announced in the release notes ahead.
  2. A key minted with only `bot` (the webapp's key type) subscribes to its own meeting over `/ws`:
     one valid key sends bots and reads what it owns (one_key_scope, 30 Oct 2026).
  3. The boundary is ownership: that key gets nothing for another user's meeting.

Runs against a live compose stack, like the rest of compat/ (opt-in, `V010_COMPAT=1`); it mints its
own users and keys and uses MOCK_BOT scenarios only.
"""
from __future__ import annotations

import json
import uuid

import pytest

from conftest import http, post_json, requires_docker
from _ws import WS

pytestmark = requires_docker

PLATFORM = "google_meet"


def _admin_headers(stack):
    return {"X-Admin-API-Key": stack.admin_token, "Content-Type": "application/json"}


def _mint(stack, scopes: str) -> str:
    email = f"client-compat-{uuid.uuid4().hex[:8]}@vexa.ai"
    code, user = http("POST", f"{stack.admin_api}/admin/users", headers=_admin_headers(stack),
                      body=json.dumps({"email": email, "name": "client-compat",
                                       "max_concurrent_bots": 3}).encode())
    assert code in (200, 201), f"create user → {code} {user}"
    code, tok = http("POST", f"{stack.admin_api}/admin/users/{user['id']}/tokens?scopes={scopes}",
                     headers=_admin_headers(stack))
    assert code in (200, 201), f"mint {scopes} → {code} {tok}"
    return tok["token"]


def _meeting(stack, key: str) -> str:
    # transcribe_enabled=false: the compose test stack has no STT, and the CC4 fail-loud guard would
    # 503 a transcribing spawn. This file must pass or fail on /ws behaviour alone, also when it runs
    # by itself (the `client-compat` step, the rc.1 negative control), so it does not lean on the STT
    # sentinel that v010_rest_compat_test.py sets when that module happens to be imported first.
    native_id = f"cc-{uuid.uuid4().hex[:3]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:3]}"
    code, body = post_json(f"{stack.gateway}/bots",
                           {"platform": PLATFORM, "native_meeting_id": native_id,
                            "bot_name": "mock:immediate-stop", "transcribe_enabled": False},
                           headers={"x-api-key": key})
    assert code == 201, f"POST /bots → {code} {body}"
    return native_id


def _ws_base(stack) -> str:
    return stack.gateway.replace("http://", "ws://").replace("https://", "wss://")


def _subscribe(ws, native_id: str) -> list:
    ws.send_text(json.dumps({"action": "subscribe",
                             "meetings": [{"platform": PLATFORM, "native_id": native_id}]}))
    frames = []
    while len(frames) < 10:
        frame = json.loads(ws.recv_text(timeout=15))
        frames.append(frame)
        if frame.get("type") in ("subscribed", "error"):
            break
    return frames


def _subscribed_to(frames, native_id: str) -> bool:
    return any(f.get("type") == "subscribed"
               and any(m.get("native_id") == native_id for m in (f.get("meetings") or []))
               for f in frames)


@pytest.fixture(scope="module")
def keys(stack):
    return {"full": _mint(stack, "bot,tx"), "bot": _mint(stack, "bot"), "other": _mint(stack, "bot,tx")}


def test_01_ws_url_key_opens_pings_and_subscribes(stack, keys):
    native_id = _meeting(stack, keys["full"])
    with WS(f"{_ws_base(stack)}/ws?api_key={keys['full']}", timeout=15) as ws:
        ws.send_text(json.dumps({"action": "ping"}))
        frames = []
        while not any(f.get("type") == "pong" for f in frames) and len(frames) < 10:
            frames.append(json.loads(ws.recv_text(timeout=10)))
        assert any(f.get("type") == "pong" for f in frames), frames
        assert not [f for f in frames if f.get("type") == "error"], frames
        sub = _subscribe(ws, native_id)
    assert _subscribed_to(sub, native_id), sub


def test_02_bot_only_key_subscribes_to_its_own_meeting(stack, keys):
    native_id = _meeting(stack, keys["bot"])
    with WS(f"{_ws_base(stack)}/ws", headers={"X-API-Key": keys["bot"]}, timeout=15) as ws:
        sub = _subscribe(ws, native_id)
    assert not [f for f in sub if f.get("error") == "insufficient_scope"], sub
    assert _subscribed_to(sub, native_id), sub


def test_03_bot_only_key_gets_nothing_for_another_users_meeting(stack, keys):
    theirs = _meeting(stack, keys["other"])
    with WS(f"{_ws_base(stack)}/ws", headers={"X-API-Key": keys["bot"]}, timeout=15) as ws:
        sub = _subscribe(ws, theirs)
    assert not _subscribed_to(sub, theirs), sub
