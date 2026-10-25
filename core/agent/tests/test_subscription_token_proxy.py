"""A MODEL SUBSCRIPTION ROUTE IS THE INSTALL'S TO ALLOW (VEXA_ALLOW_SUBSCRIPTION_TOKEN_PROXY, off).

A consumer subscription — the claude CLI's ``CLAUDE_CODE_OAUTH_TOKEN`` or credential file, Codex's
``auth.json`` — belongs to one person, and its licence forbids proxying it
(docs/docs/model-credentials-licensing.mdx). So by default a turn whose route is a subscription is
refused where the route is decided (``dispatch.apply_model_route``), before anything is spawned,
with a sentence that names the setting; no subscription credential is stamped for any worker. An
API-key route is unaffected. With the setting on, the subscription route runs as it always has.
"""
from __future__ import annotations

import json

import pytest

from control_plane import dispatch
from control_plane.model_providers import NOT_PERMITTED, ModelChoiceFault, parse
from shared.config import load_settings
from tests.model_catalogs import ENV, EXAMPLE
from tests.test_unit_foundation import VALID_INV, _proxied

SETTING = "VEXA_ALLOW_SUBSCRIPTION_TOKEN_PROXY"
_ROUTE_KEYS = (*dispatch.MODEL_AUTH_ENV_ALLOWLIST, "OPENAI_API_KEY", "CODEX_API_KEY",
               "VEXA_LLM_API_KEY", "VEXA_LLM_BASE_URL")


@pytest.fixture
def off(monkeypatch):
    """The shipped default: the setting unset, and no model credential in agent-api's environment."""
    monkeypatch.delenv(SETTING, raising=False)
    for key in _ROUTE_KEYS:
        monkeypatch.delenv(key, raising=False)


def _refused(rt, excinfo, vendor="Claude"):
    fault = excinfo.value
    assert fault.kind == NOT_PERMITTED
    assert fault.sentence() == (f"This Vexa does not pass a {vendor} subscription token to agents "
                                f"({SETTING} is off). Use a model API key, or ask the operator to "
                                "allow it.")
    assert rt.spawned == []          # refused before anything was spawned: nothing stamped or mounted


def test_the_setting_is_off_by_default(off):
    assert load_settings().allow_subscription_token_proxy is False


def test_off_the_deployments_subscription_token_is_refused(off, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    d, rt, _route = _proxied()
    with pytest.raises(ModelChoiceFault) as excinfo:
        d.dispatch(VALID_INV)
    _refused(rt, excinfo)


def test_off_a_keyless_deployment_route_is_the_subscription_and_is_refused(off):
    """No API key: the worker's CLI would sign in with the mounted subscription file."""
    d, rt, _route = _proxied()
    with pytest.raises(ModelChoiceFault) as excinfo:
        d.dispatch(VALID_INV)
    _refused(rt, excinfo)


def test_off_a_codex_subscription_is_refused_the_same_way(off):
    d, rt, _route = _proxied()
    with pytest.raises(ModelChoiceFault) as excinfo:
        d.dispatch({**VALID_INV, "runner": "codex"})
    _refused(rt, excinfo, vendor="ChatGPT (Codex)")


def test_off_an_api_key_route_runs_and_carries_no_subscription_token(off, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-api-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    d, rt, route = _proxied()
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "" and "deployment-oauth" not in str(env)
    assert env["VEXA_MODEL_ROUTE"] == "proxy" and route().subscription == ""


def test_on_the_subscription_route_runs_as_before(off, monkeypatch):
    monkeypatch.setenv(SETTING, "true")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    d, rt, route = _proxied()
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_MODEL_ROUTE"] == "subscription" and env["ANTHROPIC_BASE_URL"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "deployment-oauth"
    assert route().subscription == "claude"


def _catalog():
    return parse(json.dumps(EXAMPLE), ENV)


def test_off_the_catalogs_subscription_model_is_refused_with_its_name(off):
    env = {"VEXA_RUNNER": "claude-code"}
    with pytest.raises(ModelChoiceFault) as excinfo:
        dispatch.apply_model_route(env, {}, catalog=_catalog(), choice="claude", admin=True)
    assert (excinfo.value.model, excinfo.value.provider) == ("claude", "anthropic")
    assert SETTING in excinfo.value.sentence()


def test_off_the_catalogs_api_key_models_are_unaffected(off, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    env = {"VEXA_RUNNER": "claude-code"}
    route = dispatch.apply_model_route(env, {}, catalog=_catalog(), choice="or-sonnet", admin=True)
    assert route.model_id == "or-sonnet" and env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_on_the_catalogs_subscription_model_routes(off):
    env = {"VEXA_RUNNER": "claude-code"}
    route = dispatch.apply_model_route(env, {}, catalog=_catalog(), choice="claude", admin=True,
                                       allow_subscription=True)
    assert route.credential_source == "subscription"
