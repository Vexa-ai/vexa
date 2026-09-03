"""The card shape — PRD decision 24.6.

Founder, 2026-09-02, on a page this tool had just made (a title, a date, one bullet): *"where is
this format coming from? this is flat — not what we want."* These are what "not flat" means, stated
so it can fail: the shape per kind, both-way links, migration on touch, and idempotence — because a
renderer that is not a fixed point rewrites everybody's pages every turn and nobody keeps it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from workspaces.shared import entities as E

TEMPLATES = pathlib.Path(__file__).resolve().parents[3] / "behavior/workspaces/default/kg/templates"


def read(ws, rel):
    return (ws / rel).read_text()


def heads(text):
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.M)]


# ── the shape, per kind ──────────────────────────────────────────────────────────────────────────

def test_every_kind_renders_its_own_sections_in_order(tmp_path):
    for kind in E.KINDS:
        r = E.upsert_entity(tmp_path, kind, f"Subject {kind}", ["a dated thing"], "a source",
                            today="2026-09-02", summary="One line about it.")
        got = heads(read(tmp_path, r["path"]))
        named = list(E.CARD_SECTIONS[kind])
        assert got[:len(named)] == named, f"{kind}: {got}"
        assert got[len(named):] == ["Sources", "Timeline"]


def test_the_summary_sits_under_the_title(tmp_path):
    r = E.upsert_entity(tmp_path, "company", "Anthropic", [], "the web",
                        summary="Frontier AI lab.", fields={"what": "Builds Claude."})
    body = read(tmp_path, r["path"]).split("---\n", 2)[2]
    assert body.strip().splitlines()[:3] == ["# Anthropic", "", "Frontier AI lab."]


def test_a_summary_somebody_already_wrote_is_never_overwritten(tmp_path):
    E.upsert_entity(tmp_path, "company", "Acme", [], "s1", summary="The first sentence.")
    E.upsert_entity(tmp_path, "company", "Acme", [], "s2", summary="A tool's second guess.")
    text = read(tmp_path, "kg/entities/company/acme.md")
    assert "The first sentence." in text and "A tool's second guess." not in text


def test_a_field_lands_in_its_section_not_in_the_log(tmp_path):
    r = E.upsert_entity(tmp_path, "person", "Olga Avramenko", [], "the TSC call",
                        fields={"role": "Chairs the TSC", "cares_about": "Public-first process"})
    text = read(tmp_path, r["path"])
    role = text.split("## Role and organisation")[1].split("##")[0]
    cares = text.split("## What they care about")[1].split("##")[0]
    assert "Chairs the TSC" in role and "Chairs the TSC" not in cares
    assert "Public-first process" in cares
    assert "## Timeline" not in text        # nothing was a dated event
    assert r["filed"] == {"Role and organisation": 1, "What they care about": 1}


def test_a_plain_fact_goes_to_the_named_section_or_to_the_timeline(tmp_path):
    filed = E.upsert_entity(tmp_path, "meeting", "2026-03-02 TSC", ["Tommy was confirmed as chair."],
                            "the transcript", today="2026-09-02", section="Decided")
    assert filed["filed"] == {"Decided": 1}
    loose = E.upsert_entity(tmp_path, "meeting", "2026-03-16 TSC", ["Someone said something."],
                            "the transcript", today="2026-09-02")
    assert loose["filed"] == {"Timeline": 1}
    assert "### 2026-09-02" in read(tmp_path, loose["path"])


def test_an_unknown_section_name_falls_back_to_the_timeline_rather_than_inventing_a_heading(tmp_path):
    r = E.upsert_entity(tmp_path, "person", "Someone", ["a fact"], "a source",
                        today="2026-09-02", section="Vibes")
    assert "## Vibes" not in read(tmp_path, r["path"])
    assert r["filed"] == {"Timeline": 1}


def test_open_questions_are_the_gaps_on_the_page(tmp_path):
    r = E.upsert_entity(tmp_path, "person", "Someone", [], "a source",
                        fields={"role": "unclear from the call"},
                        open_questions=["Which team do they report into?"])
    assert "## Open questions" in read(tmp_path, r["path"])
    assert "Which team do they report into?" in read(tmp_path, r["path"])


def test_sources_are_rendered_from_frontmatter_so_the_two_cannot_disagree(tmp_path):
    E.upsert_entity(tmp_path, "company", "Acme", [], "call A", fields={"what": "a bank"})
    E.upsert_entity(tmp_path, "company", "Acme", [], "call B", fields={"what": "based in Vienna"})
    text = read(tmp_path, "kg/entities/company/acme.md")
    assert "sources: [call A, call B]" in text
    section = text.split("## Sources")[1].split("##")[0]
    assert "- call A" in section and "- call B" in section


# ── both-way links ───────────────────────────────────────────────────────────────────────────────

def test_a_company_field_links_the_person_from_the_company_page(tmp_path):
    E.upsert_entity(tmp_path, "company", "Anthropic", [], "the web", summary="Frontier AI lab.")
    r = E.upsert_entity(tmp_path, "person", "Dario Amodei", [], "meeting 105",
                        fields={"role": "CEO", "company": "[[Anthropic]]"})
    assert r["back_links"] == ["kg/entities/company/anthropic.md"]
    person = read(tmp_path, r["path"])
    company = read(tmp_path, "kg/entities/company/anthropic.md")
    assert "- [[Anthropic]] — works at" in person
    assert "- [[Dario Amodei]] — works here" in company


def test_the_reciprocal_is_written_once_however_often_it_is_stated(tmp_path):
    E.upsert_entity(tmp_path, "company", "Anthropic", [], "the web", summary="Lab.")
    for _ in range(3):
        E.upsert_entity(tmp_path, "person", "Dario Amodei", [], "meeting 105",
                        fields={"company": "[[Anthropic]]"})
    company = read(tmp_path, "kg/entities/company/anthropic.md")
    assert company.count("[[Dario Amodei]]") == 1


def test_a_link_to_a_page_that_does_not_exist_is_reported_never_minted(tmp_path):
    """A page minted from a name with no facts behind it is the invention decision 24.5 forbids.
    The edge completes the moment that page is written for a real reason."""
    r = E.upsert_entity(tmp_path, "person", "Dario Amodei", [], "meeting 105",
                        fields={"company": "[[Anthropic]]"})
    assert r["back_links"] == [] and r["links_missing"] == ["Anthropic"]
    assert not (tmp_path / "kg/entities/company/anthropic.md").exists()


def test_a_meeting_links_its_participants_both_ways(tmp_path):
    E.upsert_entity(tmp_path, "person", "Olga Avramenko", [], "s", fields={"role": "chair"})
    r = E.upsert_entity(tmp_path, "meeting", "2026-03-02 TSC", [], "the transcript",
                        fields={"participants": ["[[Olga Avramenko]]"]})
    assert "- [[Olga Avramenko]] — attendee" in read(tmp_path, r["path"])
    assert "— attended" in read(tmp_path, "kg/entities/person/olga-avramenko.md")


# ── migration ────────────────────────────────────────────────────────────────────────────────────

FLAT = ("---\ntype: company\nid: anthropic\ntitle: Anthropic\naliases: []\n"
        "created: 2026-09-02\nsources: [meeting 105 transcript]\n---\n\n# Anthropic\n\n"
        "## 2026-09-02\n\n- AI company whose CEO is Dario Amodei. — source: meeting 105 transcript\n")


def test_the_flat_page_the_founder_saw_becomes_a_card_on_its_next_touch(tmp_path):
    p = tmp_path / "kg/entities/company/anthropic.md"
    p.parent.mkdir(parents=True)
    p.write_text(FLAT)
    r = E.upsert_entity(tmp_path, "company", "Anthropic", [], "meeting 106",
                        summary="Frontier AI lab.", fields={"what": "Builds Claude."})
    assert r["migrated"] is True
    text = p.read_text()
    assert heads(text) == ["What it is", "People", "Our relationship", "Sources", "Timeline"]
    # the log is preserved, where a log belongs
    assert "### 2026-09-02" in text
    assert "AI company whose CEO is Dario Amodei." in text.split("## Timeline")[1]


def test_migration_does_not_need_new_facts(tmp_path):
    p = tmp_path / "kg/entities/company/anthropic.md"
    p.parent.mkdir(parents=True)
    p.write_text(FLAT)
    r = E.upsert_entity(tmp_path, "company", "Anthropic",
                        ["AI company whose CEO is Dario Amodei."], "meeting 105 transcript")
    assert r["migrated"] is True and r["changed"] is True
    assert "## What it is" in p.read_text()


def test_a_section_a_human_wrote_survives_the_render(tmp_path):
    p = tmp_path / "kg/entities/person/jane-liu.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntype: person\nid: jane-liu\ntitle: Jane Liu\nself: true\n---\n\n"
                 "# Jane Liu\n\n## My own notes\n\n- do not lose this\n")
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message", fields={"role": "founder"})
    text = p.read_text()
    assert "## My own notes" in text and "do not lose this" in text
    assert "self: true" in text


# ── idempotence ──────────────────────────────────────────────────────────────────────────────────

def test_the_same_call_twice_writes_nothing_the_second_time(tmp_path):
    args = dict(summary="Frontier AI lab.", fields={"what": "Builds Claude."},
                open_questions=["How many people?"])
    E.upsert_entity(tmp_path, "company", "Anthropic", [], "the web", **args)
    before = read(tmp_path, "kg/entities/company/anthropic.md")
    again = E.upsert_entity(tmp_path, "company", "Anthropic", [], "the web", **args)
    assert again["changed"] is False
    assert read(tmp_path, "kg/entities/company/anthropic.md") == before


def test_render_is_a_fixed_point(tmp_path):
    """Parse → render must not move a page it has already rendered. A renderer that is not a fixed
    point rewrites every page on every turn, and the diff of a workspace stops meaning anything."""
    E.upsert_entity(tmp_path, "person", "Olga Avramenko", ["a dated thing"], "a source",
                    today="2026-09-02", summary="Chairs the TSC.",
                    fields={"role": "chair", "cares_about": "process"},
                    open_questions=["Where do they sit?"])
    p = tmp_path / "kg/entities/person/olga-avramenko.md"
    once = p.read_text()
    fm, body = E.split_frontmatter(once)
    assert E.render_card(E.parse_card(body), "person", fm) == once


def test_a_repeated_fact_is_still_dropped_when_it_carries_a_different_source(tmp_path):
    E.upsert_entity(tmp_path, "company", "Acme", [], "call A", fields={"what": "A bank."})
    r = E.upsert_entity(tmp_path, "company", "Acme", [], "call B", fields={"what": "A bank."})
    assert r["facts_written"] == 0
    assert read(tmp_path, "kg/entities/company/acme.md").count("A bank.") == 1


# ── the templates are the human-readable statement of the same shape ─────────────────────────────

def test_a_template_exists_for_every_kind():
    for kind in E.KINDS:
        assert (TEMPLATES / f"{kind}.md").is_file(), kind


def test_card_shape_matches_the_templates():
    """The maps are the executable statement of the shape and the templates are the readable one.
    Nothing keeps them together but this."""
    for kind in E.KINDS:
        got = heads((TEMPLATES / f"{kind}.md").read_text())
        assert got == list(E.CARD_SECTIONS[kind]) + list(E.TAIL_SECTIONS), f"{kind}: {got}"


def test_the_tool_description_names_only_sections_the_renderer_has():
    text = E.tool_sections_text()
    for kind in E.KINDS:
        for head in E.CARD_SECTIONS[kind]:
            assert head in text
        for field in E.FIELD_SECTION[kind]:
            assert E.FIELD_SECTION[kind][field] in E.CARD_SECTIONS[kind]


def test_the_generated_section_list_matches_its_declared_file():
    """`shared/entity_sections.v1.txt` is what the MCP's `entity_upsert` description must state.

    The two things that have to agree live in different trees — the renderer here, the description
    in the MCP — so they agree through a committed file instead of one importing or parsing the
    other. This half says the file still matches the generator; the MCP's own suite says its
    description carries every line."""
    decl = pathlib.Path(__file__).resolve().parents[1] / "shared" / "entity_sections.v1.txt"
    body = "\n".join(ln for ln in decl.read_text().splitlines() if not ln.startswith("#"))
    assert body.strip() == E.tool_sections_text().strip()

def test_the_no_tool_fallback_describes_the_card_and_not_a_log():
    """A dispatch with no delegation token has no `entity_upsert` at all, so the phase writes the
    file itself. That text taught the FLAT page for one build: measured on the offline A/B, every
    page the fallback produced was a heading, a date and a paragraph, while the tool path rendered
    cards. A fallback that produces a different shape is a second format nobody asked for."""
    from worker.engine import entity_file_shape
    text = entity_file_shape()
    for kind in E.KINDS:
        for head in E.CARD_SECTIONS[kind]:
            assert head in text, f"{kind}/{head}"
    for tail in E.TAIL_SECTIONS:
        assert tail in text, tail
    assert "never a dated log" in text
