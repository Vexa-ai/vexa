"""PRD decisions 32/35, server half — a button press becomes an admin-owned preset, or nothing.

The rule under test is the same one scaffolds enforce: the WIRE carries a kind and its arguments,
the WORDS live in `_global/asks/`. Anything else means whoever can make a client send an intent can
drive the recipient's agent.
"""
from __future__ import annotations

from control_plane.chat_intents import INTENT_PRESETS, SILENT_KINDS, preset_for, tokens_for


def test_every_known_kind_maps_to_a_preset_name_and_nothing_else_does():
    for kind, name in INTENT_PRESETS.items():
        assert preset_for({"kind": kind}) == name
    assert preset_for({"kind": "rm -rf"}) is None
    assert preset_for({"kind": "../../etc/passwd"}) is None
    assert preset_for({"kind": ""}) is None
    assert preset_for({}) is None
    assert preset_for(None) is None
    assert preset_for("explore") is None


def test_a_kind_is_matched_case_and_space_insensitively():
    assert preset_for({"kind": " Explore "}) == "explore"


def test_highlight_is_the_silent_one():
    """The founder's correction: pressing Highlight "silently" requests the terms. A visible bubble
    would be the product narrating its own plumbing to somebody who pressed a button."""
    assert SILENT_KINDS == frozenset({"highlight"})
    assert "explore" not in SILENT_KINDS


def test_the_tokens_are_the_intents_own_fields_as_plain_strings():
    t = tokens_for({"kind": "explore", "term": "Kaar Tech", "meeting": "41", "segment": "s7"})
    assert t["term"] == "Kaar Tech" and t["meeting"] == "41" and t["segment"] == "s7"


def test_an_absent_value_renders_as_nothing_never_as_the_word_None():
    """`substitute` leaves an UNKNOWN token standing so an admin sees their typo. A token that is
    known and empty must render as nothing — otherwise the founder reads the literal `None` in his
    own chat, which is the placeholder-spoken-with-confidence failure one layer down."""
    t = tokens_for({"kind": "highlight", "meeting": "41"})
    assert t["since"] == "" and t["term"] == "" and t["path"] == ""
    assert "None" not in "".join(t.values())
