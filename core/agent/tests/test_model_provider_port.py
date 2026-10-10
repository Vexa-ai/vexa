"""THE PROVIDER PORT: ONE CATALOG ENTRY IN, ONE ROUTE OUT — per adapter, and per person.

Each adapter turns a model of the operator's catalog into a `ModelRoute`: the harness that drives
it, the endpoint, where the credential comes from (and its value, when it is a secret agent-api
holds), the model's id at the provider, the extra request fields and the capabilities. Selection
decides WHICH entry a turn runs on: the chat's pick, else the person's default, else the catalog's
— and an explicit pick that cannot run is a typed refusal, never a different model.

L1: the port and the catalog over plain values; no app, no environment.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import validate_model_list
from control_plane.model_providers import (
    CRED_NONE, CRED_SECRET, CRED_SUBJECT, CRED_SUBSCRIPTION, CREDENTIAL_MISSING,
    ENDPOINT_REFUSED, NOT_CONFIGURED, NOT_PERMITTED, UNKNOWN_MODEL, ModelChoiceFault,
    RouteContext, parse, secret_from_env,
)

from tests.model_catalogs import ENV, EXAMPLE

GOLDEN = Path(__file__).resolve().parents[1] / "contracts" / "models.v1" / "golden"

OWN = {"mode": "custom", "base_url": "https://gateway.example.com/v1", "api_key": "sk-person",
       "model": "llama-4", "extra_body": '{"top_k": 4}', "runner": "openai-agent"}


def _catalog(decl=EXAMPLE, env=ENV):
    return parse(json.dumps(decl), env)


def _ctx(cfg=None, env=ENV, refuse=None, allowed=None):
    return RouteContext(subject_config=cfg or {}, secret=secret_from_env(env),
                        endpoint_refusal=refuse or (lambda *_a: None),
                        model_allowed=allowed or (lambda _m: True),
                        deployment_runner="claude-code", deployment_model="deployment-model")


# ── per adapter ─────────────────────────────────────────────────────────────────────────────────

def test_openai_compatible_self_hosted_qwen():
    r = _catalog().route("qwen3-32b", _ctx(), admin=False)
    assert (r.adapter, r.harness, r.base_url) == ("openai_compatible", "openai-agent",
                                                  "http://10.0.0.5:8000/v1")
    assert (r.credential_source, r.credential) == (CRED_NONE, "")
    assert r.provider_model == "Qwen/Qwen3-32B"
    assert json.loads(r.extra_body) == {"chat_template_kwargs": {"enable_thinking": False}}
    assert r.capabilities.context_tokens == 32768


def test_openai_compatible_with_a_secret_and_a_model_level_extra_body():
    decl = json.loads(json.dumps(EXAMPLE))
    decl["providers"]["lab-vllm"].update(auth="secret", secret_ref="env:LAB_TOKEN")
    decl["models"][0]["extra_body"] = {"top_p": 0.9}
    r = _catalog(decl, {**ENV, "LAB_TOKEN": "lab-secret"}).route("qwen3-32b", _ctx(
        env={**ENV, "LAB_TOKEN": "lab-secret"}), admin=False)
    assert (r.credential_source, r.credential, r.auth_header) == (CRED_SECRET, "lab-secret", "bearer")
    assert json.loads(r.extra_body) == {"chat_template_kwargs": {"enable_thinking": False},
                                        "top_p": 0.9}


@pytest.mark.parametrize("harness,base", [("openai-agent", "https://openrouter.ai/api/v1"),
                                          ("claude-code", "https://openrouter.ai/api")])
def test_openrouter_on_whichever_harness_the_provider_declares(harness, base):
    decl = json.loads(json.dumps(EXAMPLE))
    decl["providers"]["openrouter"]["harness"] = harness
    r = _catalog(decl).route("or-sonnet", _ctx(), admin=True)
    assert (r.harness, r.base_url, r.credential_source) == (harness, base, CRED_SECRET)
    assert r.credential == ENV["OPENROUTER_API_KEY"] and r.auth_header == "bearer"
    assert r.provider_model == "anthropic/claude-sonnet-4.5"


def test_anthropic_on_the_deployments_subscription_names_no_endpoint_and_no_key():
    r = _catalog().route("claude", _ctx(), admin=False)
    assert (r.harness, r.base_url, r.credential_source, r.credential) == (
        "claude-code", "", CRED_SUBSCRIPTION, "")


def test_anthropic_by_api_key_sends_it_as_the_api_key_header():
    decl = json.loads(json.dumps(EXAMPLE))
    decl["providers"]["anthropic"] = {"adapter": "anthropic", "auth": "secret",
                                      "secret_ref": "env:ANTHROPIC_DIRECT_KEY"}
    r = _catalog(decl).route("claude", _ctx(), admin=False)
    assert (r.base_url, r.credential, r.auth_header) == ("https://api.anthropic.com",
                                                         ENV["ANTHROPIC_DIRECT_KEY"], "x-api-key")


def test_custom_is_the_persons_own_endpoint_key_model_body_and_harness():
    r = _catalog().route("mine", _ctx(OWN), admin=False)
    assert (r.adapter, r.base_url, r.credential_source, r.credential) == (
        "custom", OWN["base_url"], CRED_SUBJECT, "sk-person")
    assert (r.provider_model, r.extra_body, r.harness) == ("llama-4", '{"top_k": 4}', "openai-agent")


def test_custom_keeps_the_deployment_model_when_the_persons_is_not_allowlisted():
    r = _catalog().route("mine", _ctx(OWN, allowed=lambda m: m != "llama-4"), admin=False)
    assert r.provider_model == "deployment-model"


def test_custom_refuses_an_endpoint_the_operator_gate_refuses():
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog().route("mine", _ctx(OWN, refuse=lambda *_a: "host not allow-listed"), admin=False)
    assert exc.value.kind == ENDPOINT_REFUSED
    assert "sk-person" not in json.dumps(exc.value.as_dict())


def test_a_keyless_own_endpoint_runs_on_openai_agent_and_never_on_claude_code():
    from control_plane import model_endpoint

    keyless = dict(OWN, api_key="", base_url="https://openrouter.ai/api/v1")   # allow-listed
    rule = model_endpoint.route_refusal
    assert _catalog().route("mine", _ctx(keyless, refuse=rule), admin=False).credential == ""
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog().route("mine", _ctx(dict(keyless, runner="claude-code"), refuse=rule), admin=False)
    assert exc.value.kind == ENDPOINT_REFUSED and "API key" in exc.value.detail


def test_a_missing_secret_is_a_typed_refusal_naming_the_reference_not_the_value():
    cat = _catalog()
    with pytest.raises(ModelChoiceFault) as exc:
        cat.route("or-sonnet", _ctx(env={}), admin=True)     # rotated out after boot
    fault = exc.value.as_dict()
    assert (fault["source"], fault["kind"], fault["model"], fault["provider"]) == (
        "model-provider", CREDENTIAL_MISSING, "or-sonnet", "openrouter")
    assert "env:OPENROUTER_API_KEY" in fault["detail"]


def test_a_route_never_shows_its_credential_in_a_repr():
    r = _catalog().route("or-sonnet", _ctx(), admin=True)
    assert ENV["OPENROUTER_API_KEY"] not in repr(r)


# ── per person: which entry ─────────────────────────────────────────────────────────────────────

def test_the_chats_own_pick_wins():
    assert _catalog().route("claude", _ctx(), admin=False).model_id == "claude"


def test_no_pick_runs_on_the_persons_own_default():
    assert _catalog().route("", _ctx({"default_model": "claude"}), admin=False).model_id == "claude"


def test_no_pick_and_no_default_of_their_own_runs_on_the_catalogs_default():
    assert _catalog().route("", _ctx(), admin=False).model_id == "qwen3-32b"


def test_a_stale_personal_default_falls_back_to_the_catalogs():
    """A stored preference never stops a turn: gone, or not theirs, it is skipped."""
    for stale in ("deleted-model", "or-sonnet"):
        assert _catalog().route("", _ctx({"default_model": stale}), admin=False).model_id == "qwen3-32b"


def test_without_a_flagged_default_the_first_model_they_may_use_is_it():
    decl = json.loads(json.dumps(EXAMPLE))
    decl["models"][0].pop("default")
    decl["models"].insert(0, decl["models"].pop(1))          # admins-only first
    assert _catalog(decl).route("", _ctx(), admin=False).model_id == "qwen3-32b"


def test_an_explicit_pick_that_is_gone_is_refused_not_replaced():
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog().route("retired-model", _ctx(), admin=False)
    assert exc.value.kind == UNKNOWN_MODEL and exc.value.http_status == 422


def test_an_admins_only_model_is_refused_to_a_member_and_runs_for_an_admin():
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog().route("or-sonnet", _ctx(), admin=False)
    assert exc.value.kind == NOT_PERMITTED and exc.value.http_status == 403
    assert _catalog().route("or-sonnet", _ctx(), admin=True).model_id == "or-sonnet"


def test_an_own_endpoint_pick_without_an_endpoint_is_refused():
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog().route("mine", _ctx(), admin=False)
    assert exc.value.kind == NOT_CONFIGURED


def test_a_catalog_that_offers_this_person_nothing_says_so():
    decl = {"providers": EXAMPLE["providers"], "models": [EXAMPLE["models"][1]]}  # admins only
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog(decl).route("", _ctx(), admin=False)
    assert exc.value.kind == NOT_CONFIGURED


def test_the_admin_check_is_asked_only_when_an_entry_is_restricted():
    decl = {"providers": EXAMPLE["providers"], "models": [EXAMPLE["models"][0]]}
    asked = []
    _catalog(decl).listing(_ctx(), admin=lambda: asked.append(1) or False)
    assert asked == []


# ── what a person sees (models.v1 ModelList) ────────────────────────────────────────────────────

def test_a_member_sees_the_golden_listing():
    """Visibility by role, pinned to the contract's golden: no admins-only model, no own-endpoint
    model until they have one, and nothing that names an endpoint or a credential."""
    listing = _catalog().listing(_ctx(), admin=False)
    validate_model_list(listing)
    assert listing == json.loads((GOLDEN / "ModelList.member.json").read_text())


def test_a_chats_listing_carries_its_own_pick():
    listing = _catalog().listing(_ctx(), admin=False, selected="claude", with_selected=True)
    validate_model_list(listing)
    assert listing == json.loads((GOLDEN / "ModelList.chat.json").read_text())


def test_an_admin_with_an_own_endpoint_sees_everything():
    listing = _catalog().listing(_ctx(OWN), admin=True)
    validate_model_list(listing)
    assert [m["id"] for m in listing["models"]] == ["qwen3-32b", "or-sonnet", "claude", "mine"]
    mine = listing["models"][-1]
    assert (mine["adapter"], mine["harness"]) == ("custom", "openai-agent")


def test_no_listing_ever_carries_an_endpoint_or_a_credential():
    text = json.dumps(_catalog().listing(_ctx(OWN), admin=True))
    for leaked in ("10.0.0.5", "openrouter.ai", "gateway.example.com", "sk-", "env:", "extra_body",
                   "enable_thinking"):
        assert leaked not in text, leaked


# ── the fault shape is the model provider's one shape ───────────────────────────────────────────

def test_a_refused_pick_travels_in_the_model_providers_fault_shape():
    """One record for "the model could not run", whoever decided it: the harness after a provider
    call (`llm.faults.ProviderFault`) or agent-api before one. Same source, same keys — so the
    terminal's one renderer (`surfaces/faults.ts`) reads both, and no client learns a second shape."""
    from llm.faults import SOURCE, ProviderFault

    refused = ModelChoiceFault(NOT_PERMITTED, model="or-sonnet", provider="openrouter",
                               detail="x", remedy="y").as_dict()
    failed = ProviderFault(kind="unpaid", provider="openrouter.ai", model="m").as_dict()
    assert set(refused) == set(failed)
    assert refused["source"] == failed["source"] == SOURCE
    assert refused["status"] is None


def test_the_pick_kinds_never_shadow_a_providers_kinds():
    from llm.faults import KINDS as PROVIDER_KINDS
    from control_plane.model_providers import KINDS as PICK_KINDS

    assert not set(PICK_KINDS) & set(PROVIDER_KINDS)
