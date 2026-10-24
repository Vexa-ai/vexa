"""«Vexa for Claude» accounts run no Vexa agent (identity's `account_mode`, founder 2026-10-24):
`Dispatcher.dispatch` refuses every trigger for them before anything is minted or spawned, and every
other account dispatches exactly as before."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import global_layer
from control_plane import llm_budget as lb
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings
from tests.test_llm_budget import _Identity, _Reader, _Runtime

CLAUDE = {"carol"}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="",
                             admin_api_url="http://admin-api.test")
    global_layer._CACHE.clear()

    def fake(url, secret, path, **_k):
        subject = path.split("/")[3]
        return {"user_id": subject, "is_admin": False, "admin_account": False,
                "account_mode": "claude" if subject in CLAUDE else "standard"}

    monkeypatch.setattr(global_layer, "_admin_api", fake)
    runtime = _Runtime()
    dispatcher = Dispatcher(settings, runtime, _Identity())
    yield TestClient(create_app(dispatcher, stream_reader=_Reader())), runtime, dispatcher
    global_layer._CACHE.clear()


def _inv(subject, trigger):
    return {"identity": {"subject": subject, "launcher": f"user:{subject}"}, "runner": "claude-code",
            "workspaces": [{"id": subject, "mode": "rw"}], "trigger": trigger,
            "context": {"kind": "none"}, "start": {"entrypoint": {"inline": "hi"}}}


@pytest.mark.parametrize("trigger", ["scheduled", "event", "transcription", "message"])
def test_every_trigger_is_refused_for_a_claude_account_and_nothing_spawns(stack, trigger):
    _, runtime, disp = stack
    with pytest.raises(lb.BudgetExhausted) as refused:
        disp.dispatch(_inv("carol", trigger))
    assert refused.value.kind == lb.ACCOUNT_AGENTS_OFF
    assert refused.value.fault()["kind"] == "refused" and refused.value.http_status == 403
    assert runtime.spawned == []


def test_a_standard_account_dispatches_as_before(stack):
    _, runtime, disp = stack
    disp.dispatch(_inv("alice", "scheduled"))
    assert len(runtime.spawned) == 1


def test_the_chat_route_answers_the_refusal_typed(stack):
    client, runtime, _ = stack
    r = client.post("/api/chat/submit", headers={"X-User-Id": "carol"}, json={"prompt": "hi"})
    assert r.status_code == 403, r.text
    assert r.json()["reason"] == lb.ACCOUNT_AGENTS_OFF
    assert "Vexa for Claude" in r.json()["detail"]
    assert runtime.spawned == []


def test_identity_unreachable_fails_open_to_the_standard_account(stack, monkeypatch):
    _, runtime, disp = stack
    global_layer._CACHE.clear()

    def down(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(global_layer, "_admin_api", down)
    disp.dispatch(_inv("carol", "event"))
    assert len(runtime.spawned) == 1


def test_the_terminal_is_told_every_agent_feature_is_off_for_a_claude_account(stack, tmp_path):
    from control_plane import features
    client, _, _ = stack
    (tmp_path / "workspaces" / "_global" / "POLICIES.md").write_text(
        "---\nfeatures: {" + ", ".join(f"{n}: on" for n in features.AGENT_FEATURES) + ", calls: on}\n---\n")
    mine = client.get("/api/features", headers={"X-User-Id": "carol"}).json()["features"]
    theirs = client.get("/api/features", headers={"X-User-Id": "alice"}).json()["features"]
    assert all(mine[n] is False for n in features.AGENT_FEATURES)
    assert all(theirs[n] is True for n in features.AGENT_FEATURES)
    assert mine["calls"] is True
