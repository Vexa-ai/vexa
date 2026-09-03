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


# ── intent.silent → the phase mark (decision 35, F51 mechanism) ───────────────────────────────────

def test_the_four_marks_are_one_string_each():
    """The literals are DUPLICATED across three modules on purpose — the worker ships in its own
    image and `chat_intents` is importless by design — so something has to pin them together or a
    rename drifts them apart silently. This is that something."""
    from control_plane import chat_intents, scaffolds, workspace_reader
    assert chat_intents.MACHINERY_MARK == scaffolds.MACHINERY_MARK
    assert chat_intents.PHASE_MARK == workspace_reader.PHASE_MARK
    assert chat_intents.SILENT_PREFIX == "[vexa-machinery] [vexa-phase:writeback] "


def test_silent_is_read_off_the_kind_never_off_the_wire():
    """`intent.silent: true` as a CLIENT field would let anyone able to mint an intent make a turn
    invisible in someone else's conversation — the same capability `opening` is a name rather than a
    string to deny. The closed set is server-side."""
    from control_plane import chat_intents
    assert chat_intents.is_silent({"kind": "highlight"}) is True
    assert chat_intents.is_silent({"kind": "HIGHLIGHT"}) is True
    assert chat_intents.is_silent({"kind": "extend"}) is False
    assert chat_intents.is_silent({"kind": "extend", "silent": True}) is False, \
        "a wire flag must not be able to hide a turn"
    for junk in (None, "highlight", {}, {"kind": None}, {"kind": "nope"}):
        assert chat_intents.is_silent(junk) is False


def test_a_silent_turn_carries_BOTH_marks_and_a_visible_one_carries_neither():
    """Machinery alone hides the prompt but SHOWS the reply; the phase mark drops the prompt and
    every agent turn after it. A silent kind needs the second — `worker/engine.py` states the
    distinction at its own definition, and it is why there are two literals rather than one flag."""
    from control_plane import chat_intents
    silent = chat_intents.SILENT_PREFIX + "[highlight] do the thing"
    assert chat_intents.MACHINERY_MARK in silent and chat_intents.PHASE_MARK in silent
    visible = "[extend] go further on the page"
    assert chat_intents.PHASE_MARK not in visible


def test_the_reader_drops_a_phase_marked_turn():
    """End of the mechanism: the mark only means anything because `history` acts on it."""
    from control_plane import chat_intents, workspace_reader
    assert workspace_reader.PHASE_MARK in chat_intents.SILENT_PREFIX


def test_every_intent_kind_has_a_preset_file():
    """A kind in the closed set with no preset degrades to the client's fallback sentence — correct,
    but silent. These four ship WITH their presets; the test is what notices if one stops."""
    import pathlib
    from control_plane import chat_intents
    asks = pathlib.Path(__file__).resolve().parents[3] / "deploy/dogfood/asks"
    for kind, preset in chat_intents.INTENT_PRESETS.items():
        assert (asks / f"{preset}.md").is_file(), f"{kind} -> {preset}.md is missing"
