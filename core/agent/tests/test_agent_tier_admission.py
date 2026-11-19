"""VEXA_AGENT_ALLOW — the agent tier is served only to the people an install names.

Unset or empty serves every signed person (the default). A list admits the user ids, email
addresses and ``@domain`` entries it names; everyone else is refused ``agent_tier_not_enabled``.
Health, the internal tier and a worker acting for an admitted person are not refused.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import agent_admission, identity_token
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings

KEY = identity_token.generate_signing_key()
INTERNAL = "agent-test-internal-secret"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _client(monkeypatch, tmp_path, allow):
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(tmp_path / "ws"))
    if allow is None:
        monkeypatch.delenv("VEXA_AGENT_ALLOW", raising=False)
    else:
        monkeypatch.setenv("VEXA_AGENT_ALLOW", allow)
    return TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity())))


def _signed(sub: str, **claims) -> dict:
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": sub, **claims})}


def _refused(r) -> bool:
    return r.status_code == 403 and r.json().get("detail") == agent_admission.REFUSAL


def _workspace_read(c, sub, **claims):
    return c.get("/api/workspace/tree", params={"slug": sub}, headers=_signed(sub, **claims))


def _chat(c, sub, **claims):
    return c.post("/api/chat", json={"message": "hi"}, headers=_signed(sub, **claims))


def test_a_person_not_on_the_list_is_refused_a_workspace_read_and_a_chat(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "u1")
    assert _refused(_workspace_read(c, "u2"))
    assert _refused(_chat(c, "u2"))


def test_a_person_on_the_list_is_served(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "u1")
    assert not _refused(_workspace_read(c, "u1"))
    assert not _refused(_chat(c, "u1"))


def test_a_worker_acting_for_an_admitted_person_is_served(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "u1")
    dlg = {"regime": "human", "workspaces": "*"}
    assert not _refused(_workspace_read(c, "u1", delegation=dlg))
    assert _refused(_workspace_read(c, "u2", delegation=dlg))


def test_health_is_served_to_anyone(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "u1")
    assert c.get("/health").status_code == 200
    assert c.get("/health", headers=_signed("u2")).status_code == 200


def test_the_internal_tier_is_not_held_to_the_list(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "u1")
    r = c.get("/api/chat/order", headers={"X-User-Id": "u2", "X-Internal-Secret": INTERNAL})
    assert r.status_code == 200


@pytest.mark.parametrize("allow", [None, "", "*"])
def test_unset_empty_or_star_serves_everyone(monkeypatch, tmp_path, allow):
    c = _client(monkeypatch, tmp_path, allow)
    for sub in ("u1", "u2"):
        assert not _refused(_workspace_read(c, sub))
        assert not _refused(_chat(c, sub))


def test_a_domain_entry_admits_every_address_there(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "u1, @Example.Test")
    assert not _refused(_workspace_read(c, "u7", email="Ada@example.test"))
    assert _refused(_workspace_read(c, "u8", email="ada@elsewhere.test"))


def test_email_entries_are_case_insensitive(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "Ada@Example.test")
    assert not _refused(_workspace_read(c, "u9", email="ada@example.TEST"))
