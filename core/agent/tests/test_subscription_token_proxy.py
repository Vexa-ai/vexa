"""A MODEL SUBSCRIPTION ROUTE IS THE COMPANY'S TO ALLOW — `subscription_token_proxy` in
`_global/POLICIES.md`, `deny` by default.

A consumer subscription — the claude CLI's ``CLAUDE_CODE_OAUTH_TOKEN`` or credential file, Codex's
``auth.json`` — belongs to one person, and its licence forbids proxying it
(docs/docs/model-credentials-licensing.mdx). So by default a turn whose route is a subscription is
refused where the route is decided (``dispatch.apply_model_route``), before anything is spawned,
with a sentence that names the policy; no subscription credential is stamped for any worker, and no
worker carries the subscription route the runtime hands a subscription file to. An API-key route is
unaffected. With the admin's ``allow``, the subscription route runs as it always has.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from control_plane import budgets, dispatch
from control_plane.model_providers import NOT_PERMITTED, ModelChoiceFault, parse
from shared.config import load_settings
from tests.model_catalogs import ENV, EXAMPLE
from tests.test_unit_foundation import VALID_INV, _proxied

POLICY = "subscription_token_proxy"
#: The policy's real reading (the suite's conftest allows the route for every other test).
READ_POLICY = dispatch.subscription_proxy_allowed
SEED = Path(__file__).resolve().parents[3] / "behavior" / "global" / "POLICIES.md"
_ROUTE_KEYS = (*dispatch.MODEL_AUTH_ENV_ALLOWLIST, "OPENAI_API_KEY", "CODEX_API_KEY",
               "VEXA_LLM_API_KEY", "VEXA_LLM_BASE_URL")


@pytest.fixture
def policy(monkeypatch):
    """The company's policy, read for real; no model credential in agent-api's environment. Returns
    a setter that writes the admin's answer into this test's `_global/POLICIES.md`."""
    monkeypatch.setattr(dispatch, "subscription_proxy_allowed", READ_POLICY)
    for key in _ROUTE_KEYS:
        monkeypatch.delenv(key, raising=False)

    def admin_sets(value: str) -> None:
        path = Path(os.environ["VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH"]) / "POLICIES.md"
        path.write_text(f"---\nkind: policies\n{POLICY}: {value}\n---\n\n# Policies\n")
    return admin_sets


def _refused(rt, excinfo, vendor="Claude"):
    fault = excinfo.value
    assert fault.kind == NOT_PERMITTED
    assert fault.sentence() == (f"This Vexa does not pass a {vendor} subscription token to agents "
                                f"(the company policy {POLICY} is deny). Use a model API key, or "
                                "ask the admin to allow it.")
    assert rt.spawned == []          # refused before anything was spawned: nothing stamped or mounted


def test_the_policy_is_deny_by_default_and_the_seed_says_so(policy):
    assert budgets.defaults({})[POLICY] == "deny"
    assert budgets.resolve("")[POLICY] == "deny"
    assert budgets.resolve(SEED.read_text())[POLICY] == "deny"
    assert READ_POLICY(load_settings()) is False


def test_a_person_cannot_widen_it():
    admin = budgets.resolve("---\nsubscription_token_proxy: deny\n---\n")
    assert budgets.narrow(admin, {POLICY: "allow"})[POLICY] == "deny"


def test_deny_the_deployments_subscription_token_is_refused(policy, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    d, rt, _route = _proxied()
    with pytest.raises(ModelChoiceFault) as excinfo:
        d.dispatch(VALID_INV)
    _refused(rt, excinfo)


def test_deny_a_keyless_deployment_route_is_the_subscription_and_is_refused(policy):
    """No API key: the worker's CLI would sign in with the mounted subscription file."""
    d, rt, _route = _proxied()
    with pytest.raises(ModelChoiceFault) as excinfo:
        d.dispatch(VALID_INV)
    _refused(rt, excinfo)


def test_deny_a_codex_subscription_is_refused_the_same_way(policy):
    d, rt, _route = _proxied()
    with pytest.raises(ModelChoiceFault) as excinfo:
        d.dispatch({**VALID_INV, "runner": "codex"})
    _refused(rt, excinfo, vendor="ChatGPT (Codex)")


def test_deny_an_api_key_route_runs_and_carries_no_subscription(policy, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-api-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    d, rt, route = _proxied()
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "" and "deployment-oauth" not in str(env)
    # not the subscription route: the runtime hands this worker no subscription file
    assert env["VEXA_MODEL_ROUTE"] == "proxy" and route().subscription == ""


def test_allow_the_subscription_route_runs_as_before(policy, monkeypatch):
    policy("allow")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    d, rt, route = _proxied()
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_MODEL_ROUTE"] == "subscription" and env["ANTHROPIC_BASE_URL"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "deployment-oauth"
    assert route().subscription == "claude"


def test_an_unreadable_answer_is_deny(policy):
    policy("yes-please")
    assert READ_POLICY(load_settings()) is False


def _catalog():
    return parse(json.dumps(EXAMPLE), ENV)


def test_deny_the_catalogs_subscription_model_is_refused_with_its_name(policy):
    env = {"VEXA_RUNNER": "claude-code"}
    with pytest.raises(ModelChoiceFault) as excinfo:
        dispatch.apply_model_route(env, {}, catalog=_catalog(), choice="claude", admin=True)
    assert (excinfo.value.model, excinfo.value.provider) == ("claude", "anthropic")
    assert POLICY in excinfo.value.sentence()


def test_deny_the_catalogs_api_key_models_are_unaffected(policy, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    env = {"VEXA_RUNNER": "claude-code"}
    route = dispatch.apply_model_route(env, {}, catalog=_catalog(), choice="or-sonnet", admin=True)
    assert route.model_id == "or-sonnet" and env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_allow_the_catalogs_subscription_model_routes(policy):
    env = {"VEXA_RUNNER": "claude-code"}
    route = dispatch.apply_model_route(env, {}, catalog=_catalog(), choice="claude", admin=True,
                                       allow_subscription=True)
    assert route.credential_source == "subscription"
