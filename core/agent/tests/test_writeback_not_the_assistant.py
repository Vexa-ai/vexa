"""The write-back pre-pass does not ask for a page about the assistant's own model.

Friction report (dogfood, 2026-10-10): after a turn that answered "what model is running behind the
hood here?", the pre-pass flagged the model's name as a new entity needing a page. The agent rightly
declined; the extractor should not have asked. Regression tests for the narrow filter, and for what
it must leave alone.
"""
from __future__ import annotations

from worker import engine


def _mounts(tmp_path):
    return [{"slug": "d", "path": str(tmp_path), "write": True}]


def test_the_model_answering_is_not_a_writeback_candidate(tmp_path):
    said = ["This session runs on Claude Sonnet 5, with Nora Quill's notes open."]
    out = engine.writeback_candidates(said, _mounts(tmp_path))
    assert not any(n.startswith("Claude") for n in out), out
    assert "Nora Quill" in out


def test_every_tier_spelling_is_filtered(tmp_path):
    for name in ("Claude Opus 4.8", "Claude Haiku", "Claude Code", "Claude Sonnet 5"):
        assert engine._ASSISTANT_SELF.match(name), name


def test_a_person_or_company_that_starts_with_the_same_word_is_kept(tmp_path):
    said = ["Claude Monet Gallery signed, and Claude Bernard called."]
    out = engine.writeback_candidates(said, _mounts(tmp_path))
    assert "Claude Monet Gallery" in out and "Claude Bernard" in out
