"""agent_config — the governed, workspace-driven meeting-copilot config (agents/meeting.md).

Proves the isolated parser: all defaults when absent, per-key fallback on partial frontmatter, body
becomes steering, tolerant of malformed YAML / no frontmatter — and the PROVIDER-AGNOSTIC model
governance: a model is a free string; VEXA_MEETING_MODEL → VEXA_LLM_MODEL resolve the deployment
default at call time; the OPTIONAL operator allowlist (VEXA_MODEL_ALLOWLIST) gates workspace pins.
"""
from __future__ import annotations

from pathlib import Path

from shared.agent_config import (
    DEFAULT_CADENCE_SEGMENTS,
    DEFAULT_CARD_KINDS,
    DEFAULT_POLISH_RULES,
    DEFAULT_TAG_RULES,
    default_meeting_model,
    load_meeting_config,
    model_allowlist,
)


def _write(work: Path, text: str) -> None:
    p = work / "agents" / "meeting.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _clear_model_env(monkeypatch) -> None:
    for var in ("VEXA_MEETING_MODEL", "VEXA_LLM_MODEL", "VEXA_MODEL_ALLOWLIST"):
        monkeypatch.delenv(var, raising=False)


def test_absent_file_all_defaults(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    cfg = load_meeting_config(tmp_path)
    assert cfg.enabled is True
    assert cfg.model == ""  # no env, no pin → the provider adapter's own default
    assert cfg.cadence_segments == DEFAULT_CADENCE_SEGMENTS
    assert cfg.card_kinds == list(DEFAULT_CARD_KINDS)
    assert cfg.write_meeting_doc is True
    assert cfg.steering == ""


def test_full_config_parsed(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    _write(tmp_path, (
        "---\n"
        "enabled: false\n"
        "model: any-provider/route-v9\n"
        "cadence_segments: 7\n"
        "card_kinds: [person, action]\n"
        "write_meeting_doc: false\n"
        "---\n"
        "Watch only for budget commitments. Ignore small talk.\n"
    ))
    cfg = load_meeting_config(tmp_path)
    assert cfg.enabled is False
    assert cfg.model == "any-provider/route-v9"  # free string — no vendor allowlist in code
    assert cfg.cadence_segments == 7
    assert cfg.card_kinds == ["person", "action"]
    assert cfg.write_meeting_doc is False
    assert "budget commitments" in cfg.steering


def test_default_meeting_model_env_resolution(monkeypatch):
    _clear_model_env(monkeypatch)
    assert default_meeting_model() == ""
    monkeypatch.setenv("VEXA_LLM_MODEL", "deployment-default")
    assert default_meeting_model() == "deployment-default"
    monkeypatch.setenv("VEXA_MEETING_MODEL", "meeting-override")
    assert default_meeting_model() == "meeting-override"  # meeting-specific env wins


def test_partial_frontmatter_per_key_fallback(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("VEXA_LLM_MODEL", "deployment-default")
    _write(tmp_path, "---\ncadence_segments: 2\n---\njust steering text\n")
    cfg = load_meeting_config(tmp_path)
    assert cfg.cadence_segments == 2                 # set
    assert cfg.enabled is True                       # fell back
    assert cfg.model == "deployment-default"         # fell back to env default
    assert cfg.card_kinds == list(DEFAULT_CARD_KINDS)
    assert cfg.steering == "just steering text"


def test_model_allowlist_gates_workspace_pin(tmp_path, monkeypatch):
    """With VEXA_MODEL_ALLOWLIST set, an off-list workspace pin falls back to the deployment
    default (a typo cannot silently pin an unexpected route); an on-list pin passes."""
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("VEXA_LLM_MODEL", "deployment-default")
    monkeypatch.setenv("VEXA_MODEL_ALLOWLIST", "good-model, another-model")
    assert model_allowlist() == frozenset({"good-model", "another-model"})

    _write(tmp_path, "---\nmodel: gpt-4o-mega\n---\n")
    assert load_meeting_config(tmp_path).model == "deployment-default"  # gated out

    _write(tmp_path, "---\nmodel: good-model\n---\n")
    assert load_meeting_config(tmp_path).model == "good-model"          # allowed through


def test_no_allowlist_means_any_model_passes(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    _write(tmp_path, "---\nmodel: gpt-4o-mega\n---\n")
    assert load_meeting_config(tmp_path).model == "gpt-4o-mega"


def test_bad_cadence_falls_back(tmp_path):
    _write(tmp_path, "---\ncadence_segments: not-a-number\n---\n")
    assert load_meeting_config(tmp_path).cadence_segments == DEFAULT_CADENCE_SEGMENTS
    _write(tmp_path, "---\ncadence_segments: 0\n---\n")
    assert load_meeting_config(tmp_path).cadence_segments == DEFAULT_CADENCE_SEGMENTS


def test_no_frontmatter_whole_body_is_steering(tmp_path):
    _write(tmp_path, "Just steer me, no fence here.")
    cfg = load_meeting_config(tmp_path)
    assert cfg.steering == "Just steer me, no fence here."
    assert cfg.enabled is True  # defaults otherwise


def test_malformed_yaml_falls_back_to_defaults_plus_body(tmp_path):
    _write(tmp_path, "---\nenabled: [unterminated\n  : : :\n---\nstill steers\n")
    cfg = load_meeting_config(tmp_path)
    assert cfg.enabled is True
    assert cfg.card_kinds == list(DEFAULT_CARD_KINDS)
    assert cfg.steering == "still steers"


def test_empty_steering_body(tmp_path):
    _write(tmp_path, "---\nenabled: true\n---\n")
    assert load_meeting_config(tmp_path).steering == ""


def test_polish_and_tag_rules_default_when_absent(tmp_path):
    _write(tmp_path, "---\nenabled: true\n---\n")
    cfg = load_meeting_config(tmp_path)
    assert cfg.polish_rules == DEFAULT_POLISH_RULES
    assert cfg.tag_rules == DEFAULT_TAG_RULES


def test_polish_and_tag_rules_governed_by_workspace(tmp_path):
    """Editing the workspace file overrides the POLICY (prompt-only governance)."""
    _write(tmp_path, "---\npolish_rules: Keep it terse.\ntag_rules: Only tag people.\n---\n")
    cfg = load_meeting_config(tmp_path)
    assert cfg.polish_rules == "Keep it terse."
    assert cfg.tag_rules == "Only tag people."


def test_blank_rules_fall_back_to_defaults(tmp_path):
    _write(tmp_path, "---\npolish_rules: '   '\n---\n")
    assert load_meeting_config(tmp_path).polish_rules == DEFAULT_POLISH_RULES
