"""The scope of a model-provider key route that names no model — ``model_scope_unpinned``.

A key route whose turn names no model is held to the models of the family its harness speaks
(Anthropic for claude-code, OpenAI for codex and openai-agent). That is the DEFAULT: the admin's
``_global/POLICIES.md`` may say ``any`` (whatever the endpoint serves) or list model ids. A route
that names its model is held to that model whatever the setting says. Offline: the proxy's ASGI
app over fakeredis and a mock upstream, and the dispatch with a fake runtime.
"""
from __future__ import annotations

import fakeredis
import httpx
import pytest
from starlette.testclient import TestClient

from control_plane import budgets
from control_plane import model_proxy as mp
from shared import model_grant as mg
from tests.test_model_proxy import PROXY, SECRET, Upstream, _Runtime, _dispatcher
from tests.test_unit_foundation import VALID_INV


@pytest.fixture
def world():
    r = fakeredis.FakeRedis(decode_responses=True)
    grants = mp.Grants(r, SECRET)
    upstream = Upstream()
    app = mp.create_app(grants, transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        yield grants, upstream, client


def _token(grants, env, unpinned=mp.SCOPE_RUNNER_FAMILY):
    rid = grants.put_route("agent-1-chat-main", mp.route_of(env, unpinned))
    return grants.mint("agent-1-chat-main", rid, ttl_sec=900)


def _ask(client, token, model):
    return client.post("/llm/v1/messages", headers={"authorization": f"Bearer {token}",
                                                    "anthropic-version": "2023-06-01"},
                       json={"model": model, "max_tokens": 8, "messages": []})


CLAUDE_KEY = {"VEXA_RUNNER": "claude-code", "ANTHROPIC_API_KEY": "sk-ant-api03-K"}
CODEX_KEY = {"VEXA_RUNNER": "codex", "OPENAI_API_KEY": "sk-proj-K"}


# ── the default narrows ───────────────────────────────────────────────────────────────────────────

def test_an_unpinned_key_route_is_scoped_to_its_runners_family_by_default():
    assert mp.route_of(CLAUDE_KEY).models == ("family:anthropic",)
    assert mp.route_of(CODEX_KEY).models == ("family:openai",)
    assert mp.route_of({"VEXA_RUNNER": "openai-agent", "VEXA_LLM_BASE_URL": "http://q/v1",
                        "VEXA_LLM_API_KEY": "l"}).models == ("family:openai",)


def test_the_policy_seed_and_the_reader_default_to_runner_family():
    assert budgets.defaults({})["model_scope_unpinned"] == mp.SCOPE_RUNNER_FAMILY
    assert budgets.resolve("")["model_scope_unpinned"] == mp.SCOPE_RUNNER_FAMILY


def test_a_model_outside_the_family_is_refused_with_a_typed_fault(world):
    grants, upstream, client = world
    token = _token(grants, CLAUDE_KEY)
    assert _ask(client, token, "claude-sonnet-4-5-20250929").status_code == 200
    refused = _ask(client, token, "gpt-5")
    assert refused.status_code == 403
    err = refused.json()["error"]
    assert err["type"] == mp.OUT_OF_SCOPE
    assert "gpt-5" in err["message"] and "model_scope_unpinned" in err["message"]
    assert len(upstream.requests) == 1          # the refused request never reached the provider


def test_a_codex_key_route_refuses_an_anthropic_model(world):
    grants, _upstream, client = world
    token = _token(grants, CODEX_KEY)
    assert _ask(client, token, "gpt-5.1-codex").status_code == 200
    assert _ask(client, token, "claude-opus-4-1").json()["error"]["type"] == mp.OUT_OF_SCOPE


@pytest.mark.parametrize("model,family,inside", [
    ("claude-haiku-4-5", "anthropic", True), ("anthropic/claude-sonnet-4", "anthropic", True),
    ("sonnet", "anthropic", True), ("claudex", "anthropic", False), ("gpt-5", "anthropic", False),
    ("gpt-4o-mini", "openai", True), ("o3", "openai", True), ("o4-mini", "openai", True),
    ("openai/gpt-5", "openai", True), ("codex-mini-latest", "openai", True),
    ("qwen3-27b", "openai", False), ("claude-sonnet-4-5", "openai", False), ("", "openai", False),
])
def test_the_family_rule(model, family, inside):
    assert mp.model_in_scope(model, ("family:" + family,)) is inside


# ── the admin's setting widens it ─────────────────────────────────────────────────────────────────

def test_an_explicit_any_allows_every_model(world):
    grants, _upstream, client = world
    token = _token(grants, CLAUDE_KEY, unpinned="any")
    assert mp.route_of(CLAUDE_KEY, "any").models == ()
    assert _ask(client, token, "gpt-5").status_code == 200


def test_a_listed_model_is_allowed_and_nothing_else(world):
    grants, _upstream, client = world
    token = _token(grants, CLAUDE_KEY, unpinned="gpt-5, qwen3-27b")
    assert _ask(client, token, "gpt-5").status_code == 200
    assert _ask(client, token, "claude-sonnet-4-5").json()["error"]["type"] == mp.OUT_OF_SCOPE


def test_an_unreadable_setting_is_the_narrow_default():
    assert mp.unpinned_scope("claude-code", "") == ("family:anthropic",)
    assert mp.unpinned_scope("claude-code", None) == ("family:anthropic",)
    assert mp.unpinned_scope("codex", " , ") == ("family:openai",)


def test_a_person_cannot_widen_the_admins_answer():
    admin = budgets.resolve("---\nmodel_scope_unpinned: runner_family\n---\n")
    assert budgets.narrow(admin, {"model_scope_unpinned": "any"})["model_scope_unpinned"] == \
        mp.SCOPE_RUNNER_FAMILY


# ── pinned routes are unaffected ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("setting", ["runner_family", "any", "gpt-5"])
def test_a_pinned_route_keeps_its_own_model_whatever_the_setting(setting):
    env = {**CLAUDE_KEY, "VEXA_AGENT_MODEL": "claude-sonnet-4-5"}
    assert mp.route_of(env, setting).models == ("claude-sonnet-4-5",)
    assert not mp.model_in_scope("gpt-5", mp.route_of(env, setting).models)


def test_a_subscription_route_is_not_rescoped():
    assert mp.route_of({"VEXA_RUNNER": "claude-code", "CLAUDE_CODE_OAUTH_TOKEN": "o"}).models == ()


# ── the dispatch reads the admin's setting ─────────────────────────────────────────────────────────

def _sealed_scope(monkeypatch, tmp_path, policy: "str | None"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-X")
    monkeypatch.setenv("VEXA_RUNNER", "claude-code")
    monkeypatch.delenv("VEXA_AGENT_MODEL", raising=False)
    g = tmp_path / "_global"
    g.mkdir()
    monkeypatch.setenv("VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH", str(g))
    if policy is not None:
        (g / "POLICIES.md").write_text(f"---\nkind: policies\nmodel_scope_unpinned: {policy}\n---\n")
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime()
    _dispatcher(r, rt, global_system_workspace_path=str(g)).dispatch(VALID_INV)
    env = rt.spawned[0][2]
    assert env[mg.URL_ENV] == PROXY
    return mp.Grants(r, SECRET).route(env[mg.TOKEN_ENV]).models


def test_the_dispatch_seals_the_family_scope_by_default(monkeypatch, tmp_path):
    assert _sealed_scope(monkeypatch, tmp_path, None) == ("family:anthropic",)


def test_the_dispatch_seals_any_when_the_admin_says_so(monkeypatch, tmp_path):
    assert _sealed_scope(monkeypatch, tmp_path, "any") == ()
