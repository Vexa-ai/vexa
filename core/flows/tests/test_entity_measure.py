"""The entity write-back measure in the DNA replay loop — PRD decision 24.4.

Scored offline over recorded replay rows, because that is what the scorer is: a pure function of
what a revolution collected. If these two dimensions cannot be argued with here, the number they
put on the scoreboard cannot be argued with anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval" / "dna"))

import score as S  # noqa: E402


# ── bare names ───────────────────────────────────────────────────────────────────────────────────

def test_a_bare_multi_word_name_is_counted():
    assert S.unlinked_names("Cottalango Leon agreed to chair it.") == ["Cottalango Leon"]


def test_a_wikilinked_name_is_not():
    assert S.unlinked_names("[[Cottalango Leon]] agreed to chair it.") == []


def test_a_markdown_linked_name_is_not():
    assert S.unlinked_names("[Cottalango Leon](kg/entities/person/x.md) agreed.") == []


def test_sentence_initial_words_do_not_fire():
    """A measure that counts every capitalised word is measuring English, not the product."""
    assert S.unlinked_names("The meeting ran long. Monday works for everyone.") == []


def test_headings_and_code_fences_are_formatting_not_mentions():
    note = "## Open Items\n\n```\nSony Pictures Imageworks\n```\n"
    assert S.unlinked_names(note) == []


def test_frontmatter_is_not_prose():
    assert S.unlinked_names("---\ntitle: Sony Pictures Imageworks\n---\n\nAll agreed.\n") == []


def test_the_dimension_falls_as_names_are_left_dead():
    clean = {"note": "[[Olga Avramenko]] will chair."}
    dirty = {"note": "Olga Avramenko, Cottalango Leon, Sony Pictures Imageworks, "
                     "Blue Light Card and Kaar Tech all agreed."}
    assert S.d_names_linked(clean)[0] == 1.0
    assert S.d_names_linked(dirty)[0] == 0.0
    assert S.d_names_linked({"note": ""})[0] == 0.0


# ── entities touched ─────────────────────────────────────────────────────────────────────────────

def test_three_pages_per_turn_scores_one():
    rec = {"entity_turns": 1, "entity_files": ["kg/entities/person/a.md",
                                               "kg/entities/company/b.md",
                                               "kg/entities/meeting/c.md"]}
    s, ev = S.d_entities_touched(rec)
    assert s == 1.0 and ev["per_turn"] == 3.0


def test_the_meeting_page_a_turn_writes_anyway_does_not_max_the_dimension():
    """The measured reason the target is not one: at one, the arm that wrote ONLY the meeting note
    scored the same as the arm that wrote it plus eleven entity pages."""
    only_the_note = {"entity_turns": 1, "entity_files": ["kg/entities/meeting/c.md"]}
    assert S.d_entities_touched(only_the_note)[0] < 0.5


def test_a_run_that_created_nothing_scores_zero():
    assert S.d_entities_touched({"entity_turns": 3, "entity_files": []})[0] == 0.0


def test_a_run_from_before_the_measure_is_not_scored_rather_than_scored_zero():
    """-1.0 is the harness's "not scored" and is excluded from the means. Reporting an old
    revolution as a zero would show this change improving something it never measured."""
    assert S.d_entities_touched({"note": "x"})[0] == -1.0


def test_volume_is_capped_so_a_page_per_sentence_buys_nothing():
    rec = {"entity_turns": 1, "entity_files": [f"kg/entities/person/{i}.md" for i in range(40)]}
    assert S.d_entities_touched(rec)[0] == 1.0


def test_both_dimensions_are_on_the_mechanical_list():
    assert "entities_touched" in S.MECHANICAL and "names_linked" in S.MECHANICAL
