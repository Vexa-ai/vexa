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
    assert any("secret_ref env:OPENROUTER_API_KEY is not set" in p for p in problems)


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
