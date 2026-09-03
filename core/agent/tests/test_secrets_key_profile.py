"""R-E08 — "tokens encrypted at rest" has to mean something in production.

`secret_store`'s own docstring is honest about the generated key: it sits in the same directory as
the ciphertext, so it defends against a stray file read, a mis-scoped mount or a log, and NOT
against a stolen volume or backup. The operator escape is `VEXA_SECRETS_KEY` — and it appeared in
NO compose file, no `.env.example` and no helm values, so **no shipped deployment set it** and every
deployment shipped the key beside the ciphertext while the claim said otherwise.

Two halves, and neither works alone:

* the deployment surfaces carry the key, so an operator has something to set;
* generating one is a DEVELOPMENT convenience and says so — in production an unset key is a refusal
  with a sentence naming the fix, not a silent downgrade of the property the docs assert.

`VEXA_ENV` is the profile signal this repository already uses for exactly this
(`core/meetings/services/transcription/src/transcription/main.py:154-155`,
`core/meetings/services/mcp/src/vexa_mcp/app.py:587`), defaulting to `development` — so nothing
that runs today changes, and the refusal engages precisely where the claim is load-bearing.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from control_plane import secret_store


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """The host may export VEXA_SECRETS_KEY; each test here says which world it means."""
    monkeypatch.delenv("VEXA_SECRETS_KEY", raising=False)
    monkeypatch.delenv("VEXA_ENV", raising=False)


def _keyfile(root: Path) -> Path:
    return secret_store.secrets_dir(root) / secret_store.MASTER_KEY_FILENAME


# ── production ───────────────────────────────────────────────────────────────────────────────────

def test_production_refuses_to_generate_a_key_beside_the_ciphertext(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_ENV", "production")
    with pytest.raises(secret_store.SecretStoreUnconfigured) as exc:
        secret_store.put(tmp_path, "u_jane/github", "ghp_thetoken")
    assert "VEXA_SECRETS_KEY" in str(exc.value)          # the sentence names the fix
    assert not _keyfile(tmp_path).exists()               # …and nothing was written anyway
    assert not (secret_store.secrets_dir(tmp_path) / "u_jane").exists()


def test_production_with_a_configured_key_is_an_ordinary_store(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_ENV", "production")
    monkeypatch.setenv("VEXA_SECRETS_KEY", "an-operator-held-key")
    assert secret_store.put(tmp_path, "u_jane/github", "ghp_thetoken") is True
    assert secret_store.get(tmp_path, "u_jane/github") == "ghp_thetoken"
    assert not _keyfile(tmp_path).exists()               # the key is the operator's, not ours


def test_a_read_in_production_stays_a_read_and_never_raises(tmp_path, monkeypatch):
    """The refusal belongs on the WRITE. `get` sits on the git hot path where "no credential" is an
    ordinary answer (R-E13), and turning that into an exception would be a new failure, not a fix."""
    monkeypatch.setenv("VEXA_ENV", "production")
    assert secret_store.get(tmp_path, "u_jane/github") is None
    assert secret_store.state(tmp_path, "u_jane/github") == secret_store.ABSENT
    assert not _keyfile(tmp_path).exists()


# ── development ──────────────────────────────────────────────────────────────────────────────────

def test_development_still_generates_a_key_and_says_so_loudly(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=secret_store.log.name):
        assert secret_store.put(tmp_path, "u_jane/github", "ghp_thetoken") is True
    assert secret_store.get(tmp_path, "u_jane/github") == "ghp_thetoken"
    assert _keyfile(tmp_path).exists()
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "VEXA_SECRETS_KEY" in said and "development" in said.lower()


@pytest.mark.parametrize("env", ["", "development", "dev", "local", "test", "staging"])
def test_every_non_production_profile_keeps_working(tmp_path, monkeypatch, env):
    """Only `production` refuses. A profile nobody declared, and the staging box somebody stood up
    this morning, must not fail closed on a property they never claimed."""
    if env:
        monkeypatch.setenv("VEXA_ENV", env)
    assert secret_store.put(tmp_path / env, "u_jane/github", "ghp_x") is True


# ── the other half: a deployment must have somewhere to set it ───────────────────────────────────

def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "deploy" / "compose" / "docker-compose.yml").is_file():
            return p
    raise FileNotFoundError("repo root not found")


def test_the_key_is_settable_on_every_shipped_deployment_surface():
    """The review's actual complaint, as a test: the escape hatch existed and no deployment could
    reach it. A refusal an operator cannot act on is worse than the silent generation it replaced."""
    root = _repo_root()
    surfaces = {
        "compose": root / "deploy/compose/docker-compose.yml",
        ".env.example": root / "deploy/compose/.env.example",
        "helm values": root / "deploy/helm/charts/vexa/values.yaml",
    }
    missing = [name for name, p in surfaces.items()
               if not p.is_file() or "VEXA_SECRETS_KEY" not in p.read_text()]
    assert missing == [], f"VEXA_SECRETS_KEY cannot be set on: {', '.join(missing)}"


def _compose_env(service: str) -> list[str]:
    import yaml
    doc = yaml.safe_load((_repo_root() / "deploy/compose/docker-compose.yml").read_text())
    env = ((doc.get("services") or {}).get(service) or {}).get("environment") or []
    return [str(e) for e in env] if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]


def test_agent_api_can_see_the_profile_it_is_judged_against():
    """A refusal keyed on `VEXA_ENV` is worthless if the container never receives it."""
    env = _compose_env("agent-api")
    assert any(e.startswith("VEXA_ENV=") for e in env), \
        "agent-api's compose environment does not carry VEXA_ENV"


def test_agent_api_can_be_given_the_key_it_will_ask_for():
    env = _compose_env("agent-api")
    assert any(e.startswith("VEXA_SECRETS_KEY=") for e in env), \
        "agent-api's compose environment cannot receive VEXA_SECRETS_KEY"
