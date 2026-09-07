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
    asks = pathlib.Path(__file__).resolve().parents[3] / "behavior/asks"
    for kind, preset in chat_intents.INTENT_PRESETS.items():
        assert (asks / f"{preset}.md").is_file(), f"{kind} -> {preset}.md is missing"


# ── the person's own line (Vexa-ai/vexa#1593) ────────────────────────────────────────────────────

def test_the_instruction_is_a_token_like_any_other():
    """The founder's field: the selection is the WHERE, this is the WHAT. It reaches the preset the
    same way every other argument does — as a `{{token}}`, so the WORDS around it stay admin-owned."""
    t = tokens_for({"kind": "extend", "path": "kg/plan.md", "instruction": "find the youtube link"})
    assert t["instruction"] == "find the youtube link"
    assert tokens_for({"kind": "extend", "path": "a.md"})["instruction"] == ""


def test_both_asks_carry_the_token():
    """`extend.md` and `create.md` place the line themselves. A preset that does not is handled by
    `with_instruction` below — but the two we ship should not need it."""
    import pathlib
    asks = pathlib.Path(__file__).resolve().parents[3] / "behavior/asks"
    from control_plane import chat_intents
    for name in ("extend", "create"):
        assert chat_intents.INSTRUCTION_TOKEN in (asks / f"{name}.md").read_text()


def test_a_preset_that_places_the_line_is_left_alone():
    from control_plane import chat_intents
    ask = "[extend] ... {{instruction}} ..."
    text = "[extend] ... find the youtube link ..."
    assert chat_intents.with_instruction(text, ask, {"kind": "extend", "instruction": "find the youtube link"}) == text


def test_a_preset_that_does_NOT_know_the_token_still_gets_the_line():
    """⚠ THE REASON THIS EXISTS. `preset_library.top_up` is ADDITIVE — a preset already in
    `_global/asks/` is never overwritten, because its content belongs to the admin. So every
    instance that has run Extend before today has an `extend.md` with no `{{instruction}}` in it,
    and a token-only implementation would drop the one thing the person typed, silently, between
    their keystroke and the agent."""
    from control_plane import chat_intents
    out = chat_intents.with_instruction("[extend] old preset", "[extend] old preset",
                                        {"kind": "extend", "instruction": "find the youtube link"})
    assert out.startswith("[extend] old preset")
    assert out.endswith("find the youtube link")
    assert chat_intents.INSTRUCTION_LEAD in out


def test_the_token_is_looked_for_in_the_PRESET_never_in_the_output():
    """Searching the substituted text for the line would false-positive the moment somebody types a
    word the preset already uses — and the failure would be the silent one, their sentence dropped
    because the preset happened to contain it."""
    from control_plane import chat_intents
    ask = "[extend] Read it first, in full."
    out = chat_intents.with_instruction(ask, ask, {"kind": "extend", "instruction": "in full"})
    assert out.count("in full") == 2


def test_nothing_typed_changes_nothing():
    from control_plane import chat_intents
    ask = "[extend] old preset"
    for junk in (None, {}, {"kind": "extend"}, {"kind": "extend", "instruction": ""},
                 {"kind": "extend", "instruction": "   "}, {"kind": "extend", "instruction": None}):
        assert chat_intents.with_instruction(ask, ask, junk) == ask


def test_the_line_stays_one_line():
    """The client flattens it too; the server does not trust that. A newline reaching the act text
    would break the attributed block open, and the person's words have to stay recognisably theirs."""
    from control_plane import chat_intents
    out = chat_intents.with_instruction("body", "body", {"kind": "extend", "instruction": "find the\n\nlink"})
    assert out.split(chat_intents.INSTRUCTION_LEAD)[1].strip() == "find the link"


def test_the_lead_says_whose_words_follow():
    """The whole point of the attribution: the preset is ours, this line is theirs. The client's
    fallback sentence spells the same lead (`minutes/extend.ts`), which `test_terminal_parity`-style
    drift is not guarded here — but the words are pinned so a rewrite is a deliberate act."""
    from control_plane import chat_intents
    assert chat_intents.INSTRUCTION_LEAD == (
        "They typed this on the button, in their own words — what to do with it:")


def test_the_line_survives_the_job_mark():
    """A Create/Extend act runs as a background job (Vexa-ai/vexa#1584): the mark is PREFIXED to the
    prompt and `read_job_mark` strips exactly itself, handing the rest to the job as its brief. The
    person's line is in that rest, which is the acceptance criterion of #1593 stated mechanically."""
    from control_plane import chat_intents
    from shared.marks import read_job_mark
    intent = {"kind": "extend", "workspace": "desk", "path": "kg/plan.md",
              "instruction": "find the youtube link"}
    ask = "[extend] old preset"
    prompt = chat_intents.job_prefix(intent) + chat_intents.with_instruction(ask, ask, intent)
    kind, target, brief = read_job_mark(prompt)
    assert (kind, target) == ("extend", "desk/kg/plan.md")
    assert "find the youtube link" in brief


# ── the meeting-doc variant of Extend (Vexa-ai/vexa#1598) ────────────────────────────────────────
#
# Founder, live, 2026-09-06: the meeting is ONE page with the transcript in it, and Expand on that
# page reads the transcript SINCE A CURSOR the page carries. That is a different ask, and the chain
# below is how the route reaches it without making instances that lack the file worse off than they
# were before it was written.


def test_extend_on_a_meeting_page_prefers_the_meeting_ask_and_falls_back_to_the_plain_one():
    from control_plane import chat_intents
    page = {"kind": "extend", "workspace": "desk", "path": "kg/plan.md"}
    room = {"kind": "extend", "workspace": "desk",
            "path": "kg/entities/meeting/2026-03-02-0000-dna-tsc.md", "meeting": "147"}
    assert chat_intents.presets_for(page) == ["extend"]
    assert chat_intents.presets_for(room) == ["extend-meeting", "extend"]
    # THE CHAIN'S TAIL IS THE FALLBACK, and `preset_for` keeps meaning what it always meant — the
    # name a deployment can be relied on to have.
    assert chat_intents.preset_for(room) == "extend"
    assert chat_intents.preset_for(page) == "extend"


def test_only_extend_has_a_meeting_variant_and_only_when_a_meeting_is_named():
    from control_plane import chat_intents
    # a meeting named on a kind with no variant changes nothing
    assert chat_intents.presets_for({"kind": "create", "path": "p.md", "meeting": "147"}) == ["create"]
    assert chat_intents.presets_for({"kind": "highlight", "meeting": "147"}) == ["highlight"]
    # an empty or whitespace meeting is not a meeting — the client sends the field only when the
    # PAGE declared one, and a blank would route an ordinary page into the meeting ask
    for blank in ("", "   ", None):
        assert chat_intents.presets_for({"kind": "extend", "path": "p.md", "meeting": blank}) == ["extend"]
    # and an unknown kind still produces nothing to run
    assert chat_intents.presets_for({"kind": "nonsense", "meeting": "147"}) == []
    assert chat_intents.presets_for(None) == []


def test_the_meeting_id_reaches_the_ask_as_a_token():
    """`{{meeting}}` is what `extend-meeting.md` substitutes into its transcript reads. It was
    already in `tokens_for`; this pins that the meeting-doc act actually carries one."""
    from control_plane import chat_intents
    tokens = chat_intents.tokens_for({"kind": "extend", "path": "m.md", "meeting": "147"})
    assert tokens["meeting"] == "147"
    assert tokens["path"] == "m.md"


def test_the_meeting_ask_ships_in_the_image_library():
    """A chain whose first name is in no library is a chain that never runs. `preset_library.top_up`
    copies every ask the image carries into `_global/asks/`, so shipping the file IS the wiring —
    and this is the test that fails if it is deleted or renamed."""
    import pathlib
    from control_plane import chat_intents
    asks = pathlib.Path(__file__).resolve().parents[3] / "behavior" / "asks"
    for name in chat_intents.INTENT_VARIANTS.values():
        body = (asks / f"{name}.md").read_text(encoding="utf-8")
        assert "{{meeting}}" in body and "{{path}}" in body
        # the two rules whose absence is invisible: read since the cursor, never touch the widget
        assert "transcript_cursor" in body
        assert "vexa:transcript" in body
