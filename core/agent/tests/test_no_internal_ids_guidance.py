"""The agent never says an internal id to a person (founder 2026-10-16: "Your test lines in meeting 146
reached me"): a call is named by its title, a workspace by its name."""
from worker import engine


def test_the_voice_rules_out_internal_ids():
    text = engine.voice_preamble().lower()
    assert "never mention internal ids" in text
    assert "meeting numbers" in text and "slugs" in text and "session ids" in text
    assert "by its title" in text
