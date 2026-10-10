"""The model-endpoint gate (F84 · F93) — the credential must never ride to a foreign host.

The reproduction the sub-review recorded is `test_a_custom_endpoint_never_inherits_the_deployment_token`:
before the fix, `mode=custom` + a subject base_url + an EMPTY api_key left ANTHROPIC_AUTH_TOKEN
absent from the dispatch env, and `build_unit_env`'s MODEL_AUTH_ENV_ALLOWLIST backfill then copied
agent-api's OWN token in beside the subject's URL.
"""
from __future__ import annotations

import json

import pytest

from control_plane import model_endpoint
from control_plane.api import _has_custom_model_endpoint
from control_plane.config_test import run_models_test
from control_plane.dispatch import overlay_model_config

ALLOWED = "https://api.anthropic.com"


def _overlay(config, env=None, **kw):
    out = dict(env or {})
    overlay_model_config(out, config, **kw)
    return out


# ── F84: the credential pairing ───────────────────────────────────────────────────────────────────

def test_a_custom_endpoint_never_inherits_the_deployment_token(monkeypatch):
    """THE REPRODUCTION. A subject supplies a URL and NO key; the deployment holds one. Every
    credential the worker would put on a request to that URL must be stamped EMPTY, because the
    backfill fills only what is absent — and an explicit "" is not absent."""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "deployment-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-api-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    env = _overlay({"mode": "custom", "base_url": ALLOWED, "api_key": ""})
    assert env["ANTHROPIC_BASE_URL"] == ALLOWED
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                "VEXA_LLM_API_KEY"):
        assert key in env, f"{key} must be stamped (absence is what the backfill fills)"
        assert env[key] == ""


def test_the_backfill_cannot_reach_a_custom_endpoints_slot(monkeypatch):
    """End to end through the backfill itself: the deployment's token is present in agent-api's
    environment and still does not reach a dispatch whose endpoint is the subject's."""
    from shared.config import Settings

    from control_plane.dispatch import build_unit_env

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "deployment-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "deployment-oauth")
    settings = Settings()
    inv = {"identity": {"subject": "u_1", "launcher": "user:u_1"}, "runner": "claude-code",
           "workspaces": [{"id": "u_1", "mode": "rw"}], "trigger": "message",
           "start": {"entrypoint": {"inline": "hi"}}}
    env = build_unit_env(settings, inv, unit_id="unit-1", token="tok",
                         model_config={"mode": "custom", "base_url": ALLOWED, "api_key": ""})
    assert env["ANTHROPIC_BASE_URL"] == ALLOWED
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_a_subject_supplied_key_still_wins():
    env = _overlay({"mode": "custom", "base_url": ALLOWED, "api_key": "sk-subject"})
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-subject"
    assert env["ANTHROPIC_API_KEY"] == "sk-subject"


def test_custom_mode_without_an_endpoint_stays_inert():
    env = _overlay({"mode": "custom", "base_url": "", "api_key": "sk-subject"})
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env


# ── F84: the allow-list ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://admin-api:8001",             # our own control plane
    "http://redis:6379",                 # our own data plane
    "http://169.254.169.254/latest",     # cloud metadata
    "http://127.0.0.1:8001",
    "http://localhost:8001",
    "http://10.0.0.5:8000",
    "file:///etc/passwd",
    "gopher://evil.example.com",
    "https://evil.example.com/v1",
])
def test_a_non_allowlisted_endpoint_is_refused(url):
    assert model_endpoint.refuse_reason(url, env={}) is not None
    env = _overlay({"mode": "custom", "base_url": url, "api_key": "sk"})
    assert "ANTHROPIC_BASE_URL" not in env, f"{url} must never be stamped"
    # Refused means the deployment's route applies in full, and the subject's key rides nowhere.
    assert not any(k.startswith("VEXA_LLM_") for k in env)
    assert "sk" not in env.values()


@pytest.mark.parametrize("url", ["https://api.anthropic.com", "https://openrouter.ai/api/v1"])
def test_the_default_allow_list_admits_the_two_hosted_gateways(url):
    assert model_endpoint.refuse_reason(url, env={}) is None


def test_no_private_address_is_a_product_default():
    """A LAN inference box is one deployment's, named by its operator — the default list carries no
    address a subject could reach only on somebody's private network."""
    assert all(not model_endpoint._needs_literal(h) for h in model_endpoint.DEFAULT_ALLOW)
    assert model_endpoint.refuse_reason("http://192.168.1.6:8001/v1", env={}) is not None
    assert model_endpoint.refuse_reason("http://192.168.1.6:8001/v1",
                                        env={"VEXA_MODEL_BASE_URL_ALLOW": "192.168.1.6"}) is None


def test_the_deployments_own_gateway_is_always_allowed():
    env = {"ANTHROPIC_BASE_URL": "https://gw.example.test/v1"}
    assert model_endpoint.refuse_reason("https://gw.example.test/v1", env=env) is None


def test_an_operator_allow_list_replaces_the_defaults():
    env = {model_endpoint.ALLOW_ENV: "*.vllm.example.test"}
    assert model_endpoint.refuse_reason("https://a.vllm.example.test/v1", env=env) is None
    assert model_endpoint.refuse_reason("https://api.anthropic.com", env=env) is not None


def test_a_wildcard_never_reaches_the_deployments_own_network():
    """`*` is a statement about the public internet. Reaching `redis` or a private address takes an
    exact literal entry, so a lazy allow-list cannot re-open the SSRF."""
    wide = {model_endpoint.ALLOW_ENV: "*"}
    assert model_endpoint.refuse_reason("http://redis:6379", env=wide) is not None
    assert model_endpoint.refuse_reason("http://169.254.169.254/", env=wide) is not None
    exact = {model_endpoint.ALLOW_ENV: "*,192.168.1.9"}
    assert model_endpoint.refuse_reason("http://192.168.1.9:8000", env=exact) is None


def test_a_refusal_files_friction():
    filed: list[dict] = []
    env = _overlay({"mode": "custom", "base_url": "http://redis:6379", "api_key": "sk"},
                   friction=filed.append, subject="u_7")
    assert "ANTHROPIC_BASE_URL" not in env
    assert len(filed) == 1
    assert filed[0]["kind"] == "refusal" and filed[0]["subject"] == "u_7"
    assert "redis" in filed[0]["context"]["error"]


def test_a_refusal_threads_the_session_through(monkeypatch):
    """#1510: the friction carrier this record is published onto refuses a report with no
    session, so overlay_model_config must hand refusal_friction whatever it was given."""
    filed: list[dict] = []
    _overlay({"mode": "custom", "base_url": "http://redis:6379", "api_key": "sk"},
             friction=filed.append, subject="u_7", session="dispatch-u_7")
    assert filed[0]["session"] == "dispatch-u_7"


def test_a_broken_friction_sink_never_breaks_a_dispatch():
    def boom(_rec):
        raise RuntimeError("store down")

    env = _overlay({"mode": "custom", "base_url": "http://redis:6379", "api_key": "sk"},
                   friction=boom)
    assert "ANTHROPIC_BASE_URL" not in env


# ── F93: one predicate, three call sites ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cfg,expected", [
    ({"mode": "custom", "base_url": ALLOWED}, True),
    ({"mode": "custom", "base_url": "  "}, False),
    ({"mode": "subscription", "base_url": ALLOWED}, False),
    ({}, False),
])
def test_the_three_spellings_are_one(cfg, expected):
    assert model_endpoint.has_custom_endpoint(cfg) is expected
    assert _has_custom_model_endpoint(cfg) is expected


def test_the_test_button_refuses_what_the_dispatch_would_refuse():
    """The Test button must not green an endpoint the turn cannot use."""
    calls = []

    def post(url, payload, headers):
        calls.append(url)
        return 200, "{}"

    out = run_models_test({"mode": "custom", "base_url": "http://admin-api:8001", "model": "m"},
                          env={}, post=post)
    assert out["ok"] is False and calls == []
    assert "not allow-listed" in out["summary"]


def test_the_test_button_probes_a_custom_endpoint_with_the_subjects_own_key():
    """No key on the config means no key on the request — because that is what the turn sends."""
    seen = {}

    def post(url, payload, headers):
        seen["auth"] = headers.get("Authorization")
        return 200, "{}"

    out = run_models_test({"mode": "custom", "base_url": ALLOWED, "model": "m", "api_key": ""},
                          env={"ANTHROPIC_AUTH_TOKEN": "deployment-secret"}, post=post)
    assert out["ok"] is True
    assert seen["auth"] == "Bearer "


# ── F84: the Test button takes the turn's route, and only that route ───────────────────────────
# `run_models_test` used to pick its own endpoint and key: with `mode` unset (or custom without a
# Base URL) it probed the DEPLOYMENT's ANTHROPIC_BASE_URL with the SUBJECT's stored key. It now
# reads the route `overlay_model_config` stamps, so the button and the dispatch cannot disagree.

DEPLOY_GW = "https://gw.deploy.example/v1"
_DEPLOYMENT_SECRETS = ("deployment-token", "deployment-llm-key")


def _deployment(monkeypatch, **extra) -> dict:
    """agent-api's environment: the gate reads os.environ, the button reads the env it is given."""
    env = {"ANTHROPIC_BASE_URL": DEPLOY_GW, "ANTHROPIC_AUTH_TOKEN": "deployment-token",
           "VEXA_LLM_BASE_URL": "http://192.168.1.6:8001/v1",
           "VEXA_LLM_API_KEY": "deployment-llm-key", "VEXA_AGENT_MODEL": "deployment-model",
           **extra}
    env = {k: v for k, v in env.items() if v is not None}       # None = absent
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                "VEXA_LLM_BASE_URL", "VEXA_LLM_API_KEY", "VEXA_AGENT_MODEL",
                "VEXA_MODEL_ALLOWLIST", "VEXA_MODEL_BASE_URL_ALLOW"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


def _recorder(sent, *, anthropic_dialect=True):
    def post(url, payload, headers):
        sent.append({"url": url, "payload": payload, "headers": headers})
        if not anthropic_dialect and url.endswith("/v1/messages"):
            return 404, ""
        return 200, "{}"
    return post


@pytest.mark.parametrize("config", [
    {"api_key": "sk-subject", "model": "m"},                                   # mode unset
    {"mode": "custom", "base_url": "", "api_key": "sk-subject"},               # custom, no URL
    {"mode": "subscription", "base_url": ALLOWED, "api_key": "sk-subject"},     # URL not in effect
], ids=["mode-unset", "custom-without-url", "subscription"])
def test_the_test_button_never_sends_a_stored_key_to_the_deployment(monkeypatch, config, tmp_path):
    """THE REGRESSION. No subject endpoint is in effect, so the turn runs on the deployment's
    route with the deployment's credential, and so does the probe. The stored key goes nowhere."""
    env = _deployment(monkeypatch)
    sent: list = []
    out = run_models_test(dict(config, extra_body='{"x": 1}'), env=env,
                          creds_path=str(tmp_path / "absent"), post=_recorder(sent))
    assert out["route"] == "deployment" and out["ok"] is True
    assert sent, "the deployment gateway was not probed"
    for request in sent:
        assert request["url"].startswith("https://gw.deploy.example/")
        assert request["headers"]["Authorization"] == "Bearer deployment-token"
        assert "sk-subject" not in json.dumps(request)
        assert "x" not in request["payload"]          # the subject's extra body is not in effect
    assert "nothing of yours was sent or tested" in out["summary"]
    assert "sk-subject" not in json.dumps(out)


def test_without_a_deployment_gateway_the_button_checks_the_subscription_and_sends_nothing(
        monkeypatch, tmp_path):
    """The app.dev env: no ANTHROPIC gateway, the openai-agent lane on a local box. A stored key
    with no custom endpoint is not in effect; the deployment's route is the subscription file."""
    env = _deployment(monkeypatch, ANTHROPIC_BASE_URL=None, ANTHROPIC_AUTH_TOKEN=None)
    sent: list = []
    out = run_models_test({"api_key": "sk-subject"}, env=env, creds_path=str(tmp_path / "absent"),
                          post=_recorder(sent))
    assert sent == []
    assert out["route"] == "deployment" and out["mode"] == "subscription"
    assert "nothing of yours was sent or tested" in out["summary"]


def test_an_openai_agent_deployment_route_says_its_lane_was_not_probed(monkeypatch, tmp_path):
    env = _deployment(monkeypatch, ANTHROPIC_BASE_URL=None, ANTHROPIC_AUTH_TOKEN=None)
    sent: list = []
    out = run_models_test({"runner": "openai-agent"}, env=env,
                          creds_path=str(tmp_path / "absent"), post=_recorder(sent))
    assert sent == [] and out["route"] == "deployment"
    assert "does not probe" in out["summary"]


def test_a_subject_endpoint_is_probed_with_the_subjects_route_only(monkeypatch):
    """The other direction: the subject's endpoint gets the subject's key, model and extra body,
    and no deployment credential, on both dialects the probe tries."""
    env = _deployment(monkeypatch)
    sent: list = []
    out = run_models_test({"mode": "custom", "base_url": ALLOWED, "api_key": "sk-subject",
                           "model": "z-ai/glm-5.3", "extra_body": '{"provider": {"order": ["z"]}}'},
                          env=env, post=_recorder(sent, anthropic_dialect=False))
    assert out["route"] == "subject" and out["ok"] is True
    assert [r["url"] for r in sent] == [f"{ALLOWED}/v1/messages", f"{ALLOWED}/v1/chat/completions"]
    for request in sent:
        assert request["headers"]["Authorization"] == "Bearer sk-subject"
        assert request["payload"]["model"] == "z-ai/glm-5.3"
        assert not any(secret in json.dumps(request) for secret in _DEPLOYMENT_SECRETS)
    assert sent[1]["payload"]["provider"] == {"order": ["z"]}


def test_the_probe_uses_the_model_the_turn_uses(monkeypatch):
    """The allowlist the dispatch applies applies here: a dropped subject model is not probed."""
    env = _deployment(monkeypatch, VEXA_MODEL_ALLOWLIST="deployment-model")
    sent: list = []
    run_models_test({"mode": "custom", "base_url": ALLOWED, "api_key": "sk-subject",
                     "model": "not-allowed"}, env=env, post=_recorder(sent))
    assert sent and all(r["payload"]["model"] == "deployment-model" for r in sent)


# ── F93: the route is stamped in one place ──────────────────────────────────────────────────────

def test_extra_body_is_stamped_exactly_once(monkeypatch):
    """The extra_body block was written twice, verbatim, in one function. Harmless while both
    copies wrote the same value, and precisely the shape that stops being harmless the moment one
    copy is edited. The whole subject route now has ONE writer, `subject_route_env`: the overlay
    stamps none of its keys by hand, and the helper names each key once."""
    import inspect

    from control_plane import dispatch as d

    route_keys = d.subject_route_env("", "", "")
    overlay_src = inspect.getsource(d.overlay_model_config)
    helper_src = inspect.getsource(d.subject_route_env)
    for key in route_keys:
        assert f'env["{key}"]' not in overlay_src, f"{key} is stamped outside subject_route_env"
        assert helper_src.count(f'"{key}"') == 1, key

    env = _overlay({"mode": "custom", "base_url": ALLOWED, "api_key": "k",
                    "extra_body": '{"chat_template_kwargs": {"enable_thinking": false}}'})
    assert env["VEXA_LLM_EXTRA_BODY"] == '{"chat_template_kwargs": {"enable_thinking": false}}'
