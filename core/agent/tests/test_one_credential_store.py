"""One credential store: Git tokens and deploy keys live in the credential broker (ADR-0040), and
agent-api — which runs next to the worker containers — holds none of them at rest.

agent-api reads the old local store only to hand what it holds to the broker; it can no longer
write one. Without a broker a save is a typed refusal, never a credential parked on agent-api's
volume, and a credential the broker cannot take yet is a refusal, never an unauthenticated run.
"""
from __future__ import annotations

import shutil

import pytest

from control_plane import deploy_keys, git_credentials, git_secret_store as store
from tests import legacy_git_store_fixture as legacy_fixture
from tests.test_delegation_ceiling_verbs import _build, _person


def _at_rest(root) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture
def unconfigured(monkeypatch):
    for name in ("VEXA_GIT_STORE_BROKER_URL", "VEXA_GIT_STORE_KEY_FILE", "VEXA_SECRETS_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("VEXA_GIT_STORE_BROKER_URL", "http://credential-broker:8100")
    values = {}

    def call(name, action, value=None):
        if action == "put" or action == "migrate" and name not in values:
            values[name] = value
        return {"found": name in values, "value": values.get(name)}
    monkeypatch.setattr(store, "_call", call)
    return values


def test_saving_a_token_without_the_broker_is_refused_and_writes_nothing(tmp_path, unconfigured):
    with pytest.raises(store.GitStoreUnavailable) as e:
        git_credentials.set_github_token(tmp_path, "u1", "fixture-token")
    assert "not configured" in str(e.value)
    assert _at_rest(tmp_path) == []


def test_a_deploy_key_without_the_broker_is_refused_and_writes_nothing(tmp_path, unconfigured):
    with pytest.raises(store.GitStoreUnavailable):
        deploy_keys.ensure(tmp_path, "user-u1")
    assert _at_rest(tmp_path) == []


def test_without_the_broker_and_nothing_saved_there_is_simply_no_credential(tmp_path, unconfigured):
    assert git_credentials.read_github_token(tmp_path, "u1") is None
    with deploy_keys.ssh_env(tmp_path, "user-u1") as env:
        assert env is None
    assert _at_rest(tmp_path) == []


def test_a_saved_credential_the_broker_cannot_take_is_a_refusal_not_an_anonymous_run(
        tmp_path, unconfigured, monkeypatch):
    held = legacy_fixture.put(tmp_path, "pat/u1", "fixture-token", operator_key="fixture-operator-key")
    monkeypatch.setenv("VEXA_SECRETS_KEY", "fixture-operator-key")
    with pytest.raises(store.GitStoreUnavailable):
        git_credentials.read_github_token(tmp_path, "u1")
    assert held.exists()


def test_without_the_broker_clearing_a_waiting_credential_is_the_way_out(tmp_path, unconfigured):
    legacy_fixture.put(tmp_path, "pat/u1", "fixture-token")
    assert git_credentials.set_github_token(tmp_path, "u1", "") is False
    assert git_credentials.read_github_token(tmp_path, "u1") is None
    assert not (tmp_path / ".secrets").exists()


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_with_the_broker_agent_api_holds_nothing_at_rest(tmp_path, remote):
    assert git_credentials.set_github_token(tmp_path, "u1", "fixture-token")
    made = deploy_keys.ensure(tmp_path, "user-u1")
    assert made["created"] and made["public_key"].startswith("ssh-ed25519 ")
    assert git_credentials.read_github_token(tmp_path, "u1") == "fixture-token"
    with deploy_keys.ssh_env(tmp_path, "user-u1") as env:
        assert env and "GIT_SSH_COMMAND" in env
    assert set(remote) == {"pat/u1", "deploy/user-u1.priv", "deploy/user-u1.pub"}
    assert _at_rest(tmp_path) == []


def test_agent_api_has_no_way_left_to_seal_a_credential():
    with pytest.raises(ImportError):
        from control_plane import secret_store  # noqa: F401
    from control_plane import legacy_git_store
    for writer in ("put", "seal", "master_key"):
        assert not hasattr(legacy_git_store, writer), writer


def test_legacy_entries_are_counted_never_read_out(tmp_path, unconfigured, caplog):
    legacy_fixture.put(tmp_path, "pat/u1", "fixture-token")
    legacy_fixture.put(tmp_path, "deploy/user-u1.priv", "fixture-private-key")
    (tmp_path / ".secrets" / "u2.ghtoken").write_text("fixture-plaintext")
    caplog.set_level("INFO")
    assert store.report_legacy(tmp_path) == 3
    text = caplog.text
    assert "3" in text and "fixture" not in text


def test_the_api_answers_a_typed_refusal_the_chat_can_show(monkeypatch, tmp_path, unconfigured):
    client = _build(monkeypatch, tmp_path)
    r = client.post("/api/workspace/git-token", json={"token": "fixture-token"}, headers=_person())
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["fault"] == "git_credentials_unavailable"
    assert "credential broker" in body["detail"]
    assert not (tmp_path / "workspaces" / ".secrets").exists()
