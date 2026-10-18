"""PRD decision 35 — the live transcript is a surface: terms highlighted, clickable, explorable.

Three things are proven here and they are the three things that can silently be wrong:

  * the EXTRACTOR finds the names a room said, folds the short spellings into the long ones without
    losing where the short one was said, and stays the same population `entity_upsert` writes;
  * the INDEX MATCH answers `known` from the workspaces THIS reader can open, desk first — a chip
    that resolves to a namesake in someone else's group is a chip that 404s for the person clicking;
  * the PUBLISH is the agent's second call, so a bare look-up paints nothing on anybody's screen.
"""
from __future__ import annotations

import json

from llm.claude_code import parse_stream_json
from llm.tool_events import _published_terms, _TERMS_TOOLS
from shared.terms import extract_terms, index_entries, match_known, terms_for


def seg(i, text, at=None):
    return {"id": i, "text": text, "at": at if at is not None else f"2026-09-02T10:0{i}:00Z"}


# ── the extractor ────────────────────────────────────────────────────────────────────────────────

def test_it_finds_the_names_a_room_said_in_the_order_they_were_said():
    rows = extract_terms([
        seg(1, "Northwind Labs came back on the pricing."),
        seg(2, "Robin Vale is the one who signs it."),
    ])
    assert [r["term"] for r in rows] == ["Northwind Labs", "Robin Vale"]
    assert rows[0]["segments"] == [1]
    assert rows[0]["first_at"] == "2026-09-02T10:01:00Z"


def test_one_name_said_twice_is_one_term_carrying_both_lines():
    rows = extract_terms([seg(1, "Northwind Labs asked."), seg(2, "I told Northwind Labs no.")])
    assert len(rows) == 1
    assert rows[0]["segments"] == [1, 2]


def test_a_room_that_names_people_and_companies_by_one_word_gets_its_terms():
    """⚠ THE DEFECT (friction, 2026-10-13): `transcript_terms` answered NO terms for a meeting that
    plainly named people and companies. Speech names people by first name and companies by one word;
    the notes-tuned two-word floor saw none of them. A LOOK publishes nothing — the agent picks with
    `keep` — so admitting single words costs a longer list, never a screen full of chips."""
    rows = extract_terms([
        seg(1, "I spoke with Maria yesterday and she said Siemens wants the pilot."),
        seg(2, "Then Дмитрий from Яндекс joined late, on Monday."),
        seg(3, "We should ask Priya whether Acme signed."),
    ])
    terms = [r["term"] for r in rows]
    for name in ("Maria", "Siemens", "Дмитрий", "Яндекс", "Priya", "Acme"):
        assert name in terms, (name, terms)
    # a pronoun, a sentence opener and a weekday are not names
    for noise in ("I", "Then", "We", "Monday"):
        assert noise not in terms, (noise, terms)
    assert next(r for r in rows if r["term"] == "Яндекс")["segments"] == [2]


def test_a_single_word_said_only_at_a_sentence_start_is_not_a_term():
    """A capital at the head of a sentence is grammar, not a name — unless the same word is also
    said mid-sentence somewhere in the meeting, and then every line that said it counts."""
    assert extract_terms([seg(1, "Helm shipped yesterday.")]) == []
    rows = extract_terms([seg(1, "Helm shipped yesterday."), seg(2, "We moved off Helm.")])
    assert [r["term"] for r in rows] == ["Helm"]
    assert sorted(rows[0]["segments"]) == [1, 2]
    assert rows[0]["first_at"] == "2026-09-02T10:01:00Z"


def test_a_word_inside_a_longer_name_is_not_its_own_term():
    rows = extract_terms([seg(1, "Yesterday, Robin Vale met Northwind Labs.")])
    assert [r["term"] for r in rows] == ["Robin Vale", "Northwind Labs"]


def test_a_first_name_folds_into_the_full_name_but_never_into_a_longer_word():
    rows = extract_terms([seg(1, "I asked Ana first."), seg(2, "Later, Ana Lima agreed."),
                          seg(3, "We met Anacorp too.")])
    terms = {r["term"]: r for r in rows}
    assert set(terms) == {"Ana Lima", "Anacorp"}
    assert sorted(terms["Ana Lima"]["segments"]) == [1, 2]
    assert terms["Anacorp"]["segments"] == [3]


def test_a_short_spelling_folds_into_the_long_one_and_keeps_its_line():
    """`entities._drop_prefixes` DROPS the short spelling — right for choosing which page to write,
    wrong here: the short one is a real occurrence in a real line, and dropping the segment would
    make the chip's provenance start after the person did."""
    rows = extract_terms([seg(1, "James Holl said so."), seg(2, "James Hollister confirmed it.")])
    assert [r["term"] for r in rows] == ["James Hollister"]
    assert rows[0]["segments"] == [2, 1]
    # the earlier sighting wins the timestamp — it IS when the thing was first named
    assert rows[0]["first_at"] == "2026-09-02T10:01:00Z"


def test_a_chain_of_prefixes_folds_onto_the_longest_not_onto_the_next_one_up():
    """A first-match fold would put "James" onto "James Holl", which is itself folded away — and its
    line would vanish with it. The bug is invisible from the output shape; only the count shows it."""
    rows = extract_terms([seg(1, "James Hol is here."), seg(2, "James Holl is here."),
                          seg(3, "James Hollister is here.")])
    assert [r["term"] for r in rows] == ["James Hollister"]
    assert sorted(rows[0]["segments"]) == [1, 2, 3]


def test_an_empty_room_is_no_terms_not_an_error():
    assert extract_terms([]) == []
    assert extract_terms([seg(1, "   "), {"id": 2}]) == []


def test_a_sentence_opener_is_not_part_of_a_name():
    """⚠ THE MEASURED ONE. Over the 677 segments of the DNA TSC transcript of 2026-03-02 the
    notes-tuned extractor returned 28 candidates and EIGHT were speech, not names: "But I'll",
    "So I'm", "Like I've", "So Cameron", "That's John", "And Gabe", "On DNA", "Our TAC". Prose does
    not open sentences with a capitalised function word; a transcript is nothing but sentence
    openings. The opener never becomes part of a term; the name behind it is still a name."""
    said = ["But I'll take it.", "So I'm on it.", "Like I've said.", "So Cameron agreed.",
            "That's John's call.", "And Gabe will do it.", "On DNA we agreed.", "Our TAC met."]
    assert [r["term"] for r in extract_terms([seg(i, t) for i, t in enumerate(said)])] == [
        "Cameron", "John", "Gabe", "DNA", "TAC"]


def test_stripping_the_lead_still_finds_the_name_behind_it():
    rows = extract_terms([seg(1, "So Academy Software Foundation agreed.")])
    assert [r["term"] for r in rows] == ["Academy Software Foundation"]


def test_a_stripped_opener_leaves_the_one_word_name_behind_it():
    assert [r["term"] for r in extract_terms([seg(1, "So Cameron agreed.")])] == ["Cameron"]


# ── the index ────────────────────────────────────────────────────────────────────────────────────

def test_the_index_reads_entity_pages_and_nothing_else():
    rows = index_entries("w-desk", "", [
        "kg/entities/company/northwind-labs.md",
        "kg/entities/person/robin-vale.md",
        "kg/entities/company/index.md",          # the generated listing is not an entity
        "kg/templates/person.md",                # a SHAPE is not a record
        "kg/entities/spaceship/x.md",            # not one of the five kinds
        "kg/entities/company/nested/deep.md",    # not the one shape entity_upsert writes
        "README.md",
    ])
    assert [(r["entity_id"], r["kind"]) for r in rows] == [
        ("northwind-labs", "company"), ("robin-vale", "person")]
    assert rows[0]["workspace_id"] == "w-desk"
    assert rows[0]["path"] == "kg/entities/company/northwind-labs.md"


def test_a_term_with_a_page_is_known_and_carries_its_kind():
    index = index_entries("w-desk", "", ["kg/entities/company/northwind-labs.md"])
    rows = match_known(extract_terms([seg(1, "Northwind Labs came back.")]), index)
    assert rows[0]["known"] == {"workspace_id": "w-desk", "entity_id": "northwind-labs",
                                "path": "kg/entities/company/northwind-labs.md"}
    assert rows[0]["kind"] == "company"


def test_a_term_with_no_page_anywhere_is_known_null_and_carries_no_kind():
    rows = match_known(extract_terms([seg(1, "Northwind Labs came back.")]), [])
    assert rows[0]["known"] is None
    assert "kind" not in rows[0]


def test_the_desk_wins_a_namesake_in_a_group_because_the_caller_lists_it_first():
    """Order of the index IS precedence, and the tool builds it desk → `_global` → groups. A person
    who has written about Helm on their own desk must open THEIR page, not a group's."""
    index = (index_entries("w-desk", "", ["kg/entities/company/helm-deploy.md"])
             + index_entries("w-group", "acme", ["kg/entities/project/helm-deploy.md"]))
    rows = terms_for([seg(1, "Helm Deploy shipped.")], index)
    assert rows[0]["known"]["workspace_id"] == "w-desk"
    assert rows[0]["kind"] == "company"


def test_a_page_in_a_workspace_this_reader_cannot_open_is_simply_not_in_the_index():
    """Decision 26.3 — "not yours" is an ANSWER, not an error. The tool skips a mount it cannot
    list, so the term comes back unknown and the chip offers to find out. Nothing 404s."""
    assert terms_for([seg(1, "Helm Deploy shipped.")], [])[0]["known"] is None


# ── the publish (the harness seam) ───────────────────────────────────────────────────────────────

def _use(tool, cid="c1"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": {}, "id": cid}]}})


def _result(payload, cid="c1", err=False, as_blocks=True):
    body = json.dumps(payload) if not isinstance(payload, str) else payload
    content = [{"type": "text", "text": body}] if as_blocks else body
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid, "is_error": err, "content": content}]}})


LOOKED = {"meeting": "41", "cursor": "c9", "terms": [{"term": "Northwind Labs", "known": None}], "emit": []}
PUBLISHED = {"meeting": "41", "cursor": "c9", "emit": [{"term": "Northwind Labs", "known": None}]}


def test_a_bare_lookup_publishes_nothing():
    """The agent's FIRST call reads the room. Painting its raw output would put every capitalised
    word in the meeting on the person's screen — which is the whole reason `keep` exists."""
    evs = list(parse_stream_json(iter([_use("mcp__vexa__transcript_terms"), _result(LOOKED)])))
    assert not [e for e in evs if e["type"] == "terms"]


def test_the_publish_emits_the_terms_event_after_its_result():
    evs = list(parse_stream_json(iter([_use("mcp__vexa__transcript_terms"), _result(PUBLISHED)])))
    terms = [e for e in evs if e["type"] == "terms"]
    assert terms == [{"type": "terms", "meeting": "41", "cursor": "c9",
                      "terms": [{"term": "Northwind Labs", "known": None}]}]
    kinds = [e["type"] for e in evs]
    assert kinds.index("tool-result") < kinds.index("terms")


def test_a_failed_call_paints_nothing():
    evs = list(parse_stream_json(iter([_use("mcp__vexa__transcript_terms"),
                                       _result(PUBLISHED, err=True)])))
    assert not [e for e in evs if e["type"] == "terms"]


def test_the_result_is_read_whether_it_arrives_as_a_string_or_as_blocks():
    """Claude Code emits a tool result in both shapes. A reader that handles one fails SILENTLY on
    the other, and the symptom is chips that never appear with nothing anywhere saying why."""
    for as_blocks in (True, False):
        evs = list(parse_stream_json(iter([_use("transcript_terms"),
                                           _result(PUBLISHED, as_blocks=as_blocks)])))
        assert [e["type"] for e in evs].count("terms") == 1, as_blocks


def test_another_tools_result_is_never_read_as_a_publish():
    evs = list(parse_stream_json(iter([_use("mcp__vexa__meeting_transcript"), _result(PUBLISHED)])))
    assert not [e for e in evs if e["type"] == "terms"]
    assert "mcp__vexa__meeting_transcript" not in _TERMS_TOOLS


def test_a_result_that_is_not_json_is_not_a_crash():
    assert _published_terms("could not read the transcript") is None
    assert _published_terms(None) is None
    assert _published_terms(json.dumps({"emit": "everything"})) is None
