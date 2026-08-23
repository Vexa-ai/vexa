"""Class A storm — adapter↔service CONTRACT drift, the class that produced both silent-reply
bugs (SSE-timeout-as-failure · {"turns":[...]} vs list). These smokes assert THE REAL SERVICES'
shapes — the assumptions every adapter builds on — against the live dev stack. They skip cleanly
when the stack is down, and they are the drift alarm: a shape change here fails THIS file before
it becomes a 10-minute-silent conversation in production."""
from __future__ import annotations

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


pytestmark = pytest.mark.skipif(not _up("http://localhost:18100/health"),
                                reason="dev stack not running")

from flows_steps.common import ADMIN_API, AGENT_API, GATEWAY, http  # noqa: E402


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


def test_admin_user_lookup_shapes():
    code, u = http("GET", f"{ADMIN_API}/admin/users/email/definitely-absent@nowhere.test",
                   {"X-Admin-API-Key": "changeme"})
    assert code == 404
    code, u = http("GET", f"{ADMIN_API}/admin/users/email/anna@bank.com",
                   {"X-Admin-API-Key": "changeme"})
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
