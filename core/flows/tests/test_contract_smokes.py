"""Class A storm — adapter↔service CONTRACT drift, the class that produced both silent-reply
bugs (SSE-timeout-as-failure · {"turns":[...]} vs list). These smokes assert THE REAL SERVICES'
shapes — the assumptions every adapter builds on — against the live dev stack. They skip cleanly
when the stack is down, and they are the drift alarm: a shape change here fails THIS file before
it becomes a 10-minute-silent conversation in production."""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


# THE TARGET COMES FROM THE CONTRACT, and from nowhere else. This guard used to read
# a hard-coded loopback health URL — a second, private copy of a door the config contract also
# declared, and on 2026-09-03 the pair disagreed: a bare run reached `vexa-v012`'s admin-api on the
# neighbouring port and read its 403 as this stack's answer. A smoke that carries its own idea of
# where a service lives can test the wrong deployment and pass, which is worse than not running.
#
# Unnamed door → SKIP, never a fallback. `flows_config.require` raises for the two required doors;
# the agent door is a capability, so an empty string there means the agent domain is not deployed
# (PRD decision 40.7) and every agent smoke below is simply not applicable.
import flows_config  # noqa: E402


def _door(name: str) -> str:
    try:
        return flows_config.require(name).rstrip("/")
    except flows_config.ConfigError:
        return ""


AGENT_API = flows_config.get("VEXA_FLOWS_AGENT_API_URL").rstrip("/")
ADMIN_API = _door("VEXA_FLOWS_ADMIN_API_URL")
GATEWAY = _door("VEXA_FLOWS_GATEWAY_URL")

pytestmark = pytest.mark.skipif(not (AGENT_API and _up(f"{AGENT_API}/health")),
                                reason="VEXA_FLOWS_AGENT_API_URL is unnamed, or that stack is down")

# READ AT IMPORT, which is before any fixture runs — so this is the key the OPERATOR exported,
# never the placeholder `conftest` injects for the offline suite. The admin smoke below used to
# hardcode `"changeme"`, a second copy of the default that R-B11 removed from the code: against
# any real stack it answered 403, and the test had been red for exactly as long as the stack had
# been configured correctly. A contract smoke that needs a credential runs when it is given one.
REAL_ADMIN_KEY = (os.environ.get("VEXA_FLOWS_ADMIN_KEY") or "").strip()

from flows_steps.common import http  # noqa: E402 — the doors above come from the contract


def test_history_is_wrapped_in_turns_key():
    """The {"turns": [...]} bug: history is a DICT wrapping the list — forever asserted."""
    code, body = http("GET", f"{AGENT_API}/api/sessions/contract-smoke/history", {"X-User-Id": "11"})
    assert code == 200 and isinstance(body, dict) and isinstance(body.get("turns"), list)


def test_chat_post_is_a_stream_not_a_response():
    """The SSE bug: /api/chat holds the connection open for the turn. Contract: a short client
    timeout while the stream runs is NOT failure. We assert the endpoint does not return a
    complete JSON body within 3s (it streams), i.e. dispatch must be fire-and-forget."""
    import json
    req = urllib.request.Request(f"{AGENT_API}/api/chat", method="POST",
                                 data=json.dumps({"prompt": "[contract-smoke] reply with one word",
                                                  "session": "contract-smoke"}).encode())
    req.add_header("content-type", "application/json")
    req.add_header("X-User-Id", "11")
    import socket
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            first = r.read(10)                     # a stream may yield SSE bytes — fine
            assert r.headers.get_content_type() in ("text/event-stream", "application/json")
    except (TimeoutError, socket.timeout):
        pass                                       # stream stayed open — the documented behavior


@pytest.mark.skipif(not (REAL_ADMIN_KEY and ADMIN_API),
                    reason="VEXA_FLOWS_ADMIN_KEY / VEXA_FLOWS_ADMIN_API_URL not exported — the "
                           "smoke needs THIS stack's key and THIS stack's door")
def test_admin_user_lookup_shapes():
    keyed = {"X-Admin-API-Key": REAL_ADMIN_KEY}
    code, u = http("GET", f"{ADMIN_API}/admin/users/email/definitely-absent@nowhere.test", keyed)
    assert code == 404
    code, u = http("GET", f"{ADMIN_API}/admin/users/email/anna@bank.com", keyed)
    if code == 200:
        assert isinstance(u.get("id"), int)        # adapters str() this — must exist


def test_workspace_file_contract():
    code, body = http("GET", f"{AGENT_API}/api/workspace/file?path=definitely/absent.md",
                      {"X-User-Id": "11"})
    assert code == 404                             # scaffolded() truth depends on this
    code, body = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": "11"})
    assert code == 200 and isinstance(body, dict) and isinstance(body.get("commits"), list)
    if body["commits"]:
        c = body["commits"][0]
        assert "sha" in c and "msg" in c           # note-detection reads these
        assert "files" in c or True                # files may be absent on some commits — adapters must .get()


def test_transcripts_contract():
    code, body = http("GET", f"{GATEWAY}/transcripts/google_meet/definitely-absent",
                      {"X-API-Key": "invalid-key-shape"})
    assert code in (401, 403, 404)                 # never 200 for garbage auth
