"""agent_config — the governed, workspace-driven meeting-copilot config (agents/meeting.md).

Proves the isolated parser: all defaults when absent, per-key fallback on partial frontmatter, body
becomes steering, bad-model falls back to the default, enabled=false honored, and tolerant of
malformed YAML / no frontmatter.
"""
from __future__ import annotations

from pathlib import Path

from agent_api.agent_config import (
    DEFAULT_CADENCE_SEGMENTS,
    DEFAULT_CARD_KINDS,
    DEFAULT_MEETING_MODEL,
    MODEL_ALLOWLIST,
    load_meeting_config,
)


def _write(work: Path, text: str) -> None:
    p = work / "agents" / "meeting.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_absent_file_all_defaults(tmp_path):
    cfg = load_meeting_config(tmp_path)
    assert cfg.enabled is True
    assert cfg.model == DEFAULT_MEETING_MODEL
    assert cfg.cadence_segments == DEFAULT_CADENCE_SEGMENTS
    assert cfg.card_kinds == list(DEFAULT_CARD_KINDS)
    assert cfg.write_meeting_doc is True
    assert cfg.steering == ""


def test_full_config_parsed():
    # pick a real allowlisted non-default model
    model = next(m for m in MODEL_ALLOWLIST if m != DEFAULT_MEETING_MODEL)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        _write(work, (
            "---\n"
            "enabled: false\n"
            f"model: {model}\n"
            "cadence_segments: 7\n"
            "card_kinds: [person, action]\n"
            "write_meeting_doc: false\n"
            "---\n"
            "Watch only for budget commitments. Ignore small talk.\n"
        ))
        cfg = load_meeting_config(work)
    assert cfg.enabled is False
    assert cfg.model == model
    assert cfg.cadence_segments == 7
    assert cfg.card_kinds == ["person", "action"]
    assert cfg.write_meeting_doc is False
    assert "budget commitments" in cfg.steering


def test_partial_frontmatter_per_key_fallback(tmp_path):
    _write(tmp_path, "---\ncadence_segments: 2\n---\njust steering text\n")
    cfg = load_meeting_config(tmp_path)
    assert cfg.cadence_segments == 2           # set
    assert cfg.enabled is True                 # fell back
    assert cfg.model == DEFAULT_MEETING_MODEL  # fell back
    assert cfg.card_kinds == list(DEFAULT_CARD_KINDS)
    assert cfg.steering == "just steering text"


def test_bad_model_falls_back_to_default(tmp_path):
    _write(tmp_path, "---\nmodel: gpt-4o-mega\n---\n")
    cfg = load_meeting_config(tmp_path)
    assert cfg.model == DEFAULT_MEETING_MODEL


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
