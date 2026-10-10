"""THE MODEL CATALOG IS REFUSED WHOLE AT BOOT, AND NEVER REPEATS A SECRET DOING IT.

`VEXA_MODEL_CATALOG` is the operator's declaration of the providers this deployment reaches and the
models it offers (models.v1 Catalog). A half-right catalog shows up as a person's turn failing an
hour later, so every problem is named at boot, together — and a credential pasted where a reference
belongs must not reach the boot log through the refusal that catches it.

L1: pure parsing, no app.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from control_plane.model_providers import ADAPTERS, Catalog, CatalogError, parse
from tests.model_catalogs import ENV, EXAMPLE

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "models.v1"

def _refused(decl, env=ENV) -> list[str]:
    with pytest.raises(CatalogError) as exc:
        parse(json.dumps(decl) if not isinstance(decl, str) else decl, env)
    return exc.value.problems


def _with(mutate) -> dict:
    decl = copy.deepcopy(EXAMPLE)
    mutate(decl)
    return decl


# ── the happy path and the absent catalog ───────────────────────────────────────────────────────

def test_the_worked_example_parses():
    cat = parse(json.dumps(EXAMPLE), ENV)
    assert cat.ids == ["qwen3-32b", "or-sonnet", "claude", "mine"]
    assert not cat.empty


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_no_catalog_is_the_empty_catalog(raw):
    """Unset or blank: no catalog, and every turn routes exactly as without one."""
    assert parse(raw, ENV).empty
    assert Catalog(None).empty


def test_the_committed_golden_is_the_example():
    """The contract's golden IS the worked example the docs and tests use (P8)."""
    golden = json.loads((CONTRACT / "golden" / "Catalog.self-hosted-and-openrouter.json").read_text())
    assert golden == EXAMPLE
    assert not parse(json.dumps(golden), ENV).empty


# ── the rules ───────────────────────────────────────────────────────────────────────────────────

def test_an_unknown_adapter_is_refused():
    problems = _refused(_with(lambda d: d["providers"]["lab-vllm"].update(adapter="bedrock")))
    assert any("providers/lab-vllm/adapter" in p for p in problems)


def test_the_contract_names_exactly_the_adapters_that_exist():
    """A kind is added in two places a red test ties together: its module, and its enum entry."""
    schema = json.loads((CONTRACT / "models.schema.json").read_text())
    assert set(schema["$defs"]["Adapter"]["enum"]) == set(ADAPTERS)


def test_duplicate_ids_are_refused():
    problems = _refused(_with(lambda d: d["models"].append(dict(d["models"][0], default=False))))
    assert any("'qwen3-32b' is declared twice" in p for p in problems)


def test_two_defaults_are_refused():
    problems = _refused(_with(lambda d: d["models"][2].update(default=True)))
    assert any("more than one default" in p for p in problems)


def test_a_model_on_an_undeclared_provider_is_refused():
    problems = _refused(_with(lambda d: d["models"][0].update(provider="nowhere")))
    assert any("provider 'nowhere' is not declared" in p for p in problems)


def test_a_model_that_cannot_call_tools_is_refused():
    problems = _refused(_with(lambda d: d["models"][0]["capabilities"].update(tool_calling=False)))
    assert any("tool calling" in p for p in problems)


def test_an_unset_secret_reference_is_refused_at_boot():
    problems = _refused(EXAMPLE, env={})
    assert any("secret_ref env:VEXA_MODEL_SECRET_OPENROUTER is not set" in p for p in problems)


def test_every_problem_is_named_at_once():
    def mutate(d):
        d["providers"]["lab-vllm"]["adapter"] = "bedrock"
        d["models"].append(dict(d["models"][0], default=False))
    assert len(_refused(_with(mutate))) >= 2


def test_not_json_is_refused():
    assert "not valid JSON" in _refused("{nope")[0]


# ── inline secrets: refused, and never quoted ────────────────────────────────────────────────────

PASTED = "sk-or-v1-0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("mutate", [
    lambda d: d["providers"]["openrouter"].update(api_key=PASTED),          # a field that is a key
    lambda d: d["providers"]["openrouter"].update(secret_ref=PASTED),       # a value where a ref goes
    lambda d: d["providers"]["lab-vllm"]["extra_body"].update(api_key=PASTED),
    lambda d: d["providers"]["lab-vllm"]["extra_body"].update(note=f"Bearer {PASTED}"),
    lambda d: d["models"][0].update(display_name=PASTED),
    lambda d: d["providers"]["lab-vllm"].update(base_url=f"http://u:{PASTED}@10.0.0.5:8000/v1"),
    lambda d: d["providers"]["lab-vllm"].update(base_url=f"http://10.0.0.5:8000/v1?key={PASTED}"),
], ids=["key-field", "key-as-ref", "key-in-extra-body", "bearer-in-extra-body", "key-as-name",
        "key-in-userinfo", "key-in-query"])
def test_an_inline_credential_is_refused_and_never_quoted(mutate):
    problems = _refused(_with(mutate))
    assert problems
    assert not any(PASTED in p for p in problems), problems
    with pytest.raises(CatalogError) as exc:
        parse(json.dumps(_with(mutate)), ENV)
    assert PASTED not in str(exc.value)


# ── adapter-specific declarations ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d["providers"]["lab-vllm"].pop("base_url"), "'base_url' is required"),
    (lambda d: d["providers"]["lab-vllm"].update(harness="claude-code"), "'harness' must be"),
    (lambda d: d["providers"]["openrouter"].update(auth="none"), "OpenRouter needs a key"),
    (lambda d: d["providers"]["openrouter"].update(base_url="https://elsewhere.example/v1"),
     "'base_url' is not used by the openrouter adapter"),
    (lambda d: d["providers"]["openrouter"].update(harness="claude-code",
                                                   extra_body={"x": 1}), "extra_body"),
    (lambda d: d["providers"]["anthropic"].update(auth="none"), "'auth' must be secret"),
    (lambda d: d["providers"]["own"].update(base_url="https://x.example/v1"),
     "'base_url' is not used by the custom adapter"),
    (lambda d: d["models"][3].update(model="gpt-x"), "the person's own setting"),
    (lambda d: d["models"][0].pop("model"), "'model' (the id at the provider) is required"),
])
def test_each_adapter_refuses_what_it_does_not_take(mutate, expect):
    assert any(expect in p for p in _refused(_with(mutate))), _refused(_with(mutate))


# ── effort levels: offered only where the adapter can send them (founder 2026-10-10) ──────────

def _model(decl, mid):
    return next(m for m in decl["models"] if m["id"] == mid)


@pytest.mark.parametrize("mid, efforts, said", [
    # a Qwen enable_thinking toggle is on or off — none and high, nothing between
    ("qwen3-32b", ["none", "low", "high"], "cannot send effort level(s) low as chat_template_kwargs.enable_thinking"),
    # OpenRouter's reasoning.effort stops at xhigh
    ("or-sonnet", ["high", "max"], "cannot send effort level(s) max as reasoning.effort"),
    # the claude CLI's --effort has no "none"
    ("claude", ["none", "low"], "cannot send effort level(s) none as the claude CLI's --effort"),
])
def test_a_level_the_adapter_cannot_send_is_refused_at_boot(mid, efforts, said):
    def mutate(d):
        caps = _model(d, mid).setdefault("capabilities", {})
        caps["reasoning_efforts"] = efforts
        caps.pop("default_effort", None)
    assert any(said in p for p in _refused(_with(mutate)))


def test_a_default_effort_must_be_one_of_the_models_levels():
    problems = _refused(_with(lambda d: _model(d, "or-sonnet")["capabilities"].update(default_effort="xhigh")))
    assert any("default_effort 'xhigh' is not one of its reasoning_efforts" in p for p in problems)
    problems = _refused(_with(lambda d: _model(d, "claude").update(capabilities={"default_effort": "low"})))
    assert any("default_effort is set but reasoning_efforts is not" in p for p in problems)


def test_effort_on_a_custom_entry_and_effort_control_off_openai_compatible_are_refused():
    problems = _refused(_with(lambda d: (
        _model(d, "mine").update(capabilities={"reasoning_efforts": ["low"]}),
        _model(d, "claude").update(effort_control="reasoning_effort"))))
    assert any("model 'mine': effort on a custom entry is the person's own setting" in p for p in problems)
    assert any("model 'claude': 'effort_control' is read only by the openai_compatible adapter" in p
               for p in problems)


def test_openrouter_on_claude_code_offers_no_effort_control():
    problems = _refused(_with(lambda d: d["providers"]["openrouter"].update(harness="claude-code")))
    assert any("use harness: openai-agent for effort control" in p for p in problems)


def test_an_effort_level_outside_the_vocabulary_is_a_schema_refusal():
    problems = _refused(_with(lambda d: _model(d, "claude")["capabilities"].update(reasoning_efforts=["turbo"])))
    assert any("reasoning_efforts/0" in p for p in problems)


def test_max_output_tokens_must_be_a_positive_count():
    problems = _refused(_with(lambda d: _model(d, "claude").update(max_output_tokens=0)))
    assert any("max_output_tokens" in p for p in problems)


# ── R1797-1: a catalog may name only its own secrets, never agent-api's ───────────────────────

@pytest.mark.parametrize("name", ["INTERNAL_API_SECRET", "VEXA_INTERNAL_API_SECRET", "DATABASE_URL",
                                  "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "VEXA_MODEL_SECRET_"])
def test_a_secret_ref_outside_the_catalog_prefix_is_refused_even_when_it_is_set(name):
    env = {**ENV, name: "agent-api-own-value"}
    problems = _refused(_with(lambda d: d["providers"]["openrouter"].update(secret_ref=f"env:{name}")), env)
    assert any("secret_ref must name a variable VEXA_MODEL_SECRET_<NAME>" in p for p in problems)
    assert not any("agent-api-own-value" in p for p in problems)


def test_the_resolver_never_reads_a_variable_outside_the_prefix():
    from control_plane.model_providers import secret_from_env
    resolve = secret_from_env({"INTERNAL_API_SECRET": "x", "VEXA_MODEL_SECRET_OR": "k"})
    assert resolve("env:INTERNAL_API_SECRET") == "" and resolve("env:VEXA_MODEL_SECRET_OR") == "k"
