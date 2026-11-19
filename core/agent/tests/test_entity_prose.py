"""`entity_upsert` must not rewrite what it did not write — F98, verified by a round trip.

Three mangles were reported on a page a human had authored: paragraphs collapsed into one line,
blank lines dropped, and a `##` *inside a code fence* promoted to a real heading — which cut the
fence in half and turned everything after it into sections. All three came from one decision: the
first parser read a page into a MODEL and re-rendered the whole thing, so every line it did not
understand was a line it silently normalised.

The fix is structural rather than defensive: the page is head + raw blocks, `render(parse(x)) == x`
for any input, and only the sections the tool owns are ever regenerated. The last test in this file
is that property, over random markdown.
"""
from __future__ import annotations

import random

import pytest

from workspaces.shared import entities as E


def page(body: str) -> str:
    return "---\ntype: person\nid: jane-liu\ntitle: Jane Liu\n---\n" + body


def write(ws, body: str):
    p = ws / "kg/entities/person/jane-liu.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page(body))
    return p


# ── mangle 1: paragraphs collapsed ───────────────────────────────────────────────────────────────

PROSE = """
# Jane Liu

She joined in March and runs the migration.
Her half of it is the schema; the cutover is someone else's.

She is the person to ask about the old billing table, which nobody
else has read end to end.
"""


def test_a_paragraph_is_not_collapsed_into_one_line(tmp_path):
    p = write(tmp_path, PROSE)
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message",
                    fields={"role": "runs the migration"})
    body = p.read_text().split("---\n", 2)[2]
    for line in PROSE.strip().splitlines():
        assert line in body.splitlines(), line
    assert "the schema; the cutover is someone else's." in body


def test_the_blank_line_between_paragraphs_survives(tmp_path):
    p = write(tmp_path, PROSE)
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message",
                    fields={"role": "runs the migration"})
    body = p.read_text()
    assert "someone else's.\n\nShe is the person to ask" in body


# ── mangle 2: a heading invented inside a code fence ─────────────────────────────────────────────

FENCED = """
# Jane Liu

How she wants the note laid out:

```markdown
## Decided
- one bullet each

## Open
- and nothing else
```

That is the whole convention.
"""


def test_a_hash_inside_a_code_fence_is_not_a_heading(tmp_path):
    p = write(tmp_path, FENCED)
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message",
                    fields={"role": "runs the migration"})
    body = p.read_text()
    # the fence comes back whole, in order, with its contents untouched
    assert "```markdown\n## Decided\n- one bullet each\n\n## Open\n- and nothing else\n```" in body
    assert "That is the whole convention." in body
    # and the tool did not adopt the fence's headings as sections of its own
    card = E.parse_card(body.split("---\n", 2)[2])
    assert card.index("Decided") < 0 and card.index("Open") < 0


def test_a_tilde_fence_is_opaque_too(tmp_path):
    p = write(tmp_path, "\n# Jane Liu\n\n~~~\n## not a heading\n~~~\n")
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    assert "~~~\n## not a heading\n~~~" in p.read_text()


def test_an_html_comment_is_opaque(tmp_path):
    """The seed's own templates carry `<!-- the web: link every entity … -->` with prose inside."""
    p = write(tmp_path, "\n# Jane Liu\n\n<!--\n## a note to myself\nnot markup\n-->\n")
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    assert "<!--\n## a note to myself\nnot markup\n-->" in p.read_text()


# ── only the tool's own sections are regenerated ─────────────────────────────────────────────────

def test_a_human_section_comes_back_byte_for_byte(tmp_path):
    body = ("\n# Jane Liu\n\nA line.\n\n## How I think about this\n\n"
            "Two sentences, on\ntwo lines.\n\n  indented, deliberately\n\n"
            "## Role and organisation\n\n- something they wrote themselves\n")
    p = write(tmp_path, body)
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "a source",
                    fields={"cares_about": "the migration"})
    got = p.read_text()
    assert "## How I think about this\n\nTwo sentences, on\ntwo lines.\n\n  indented, deliberately\n" in got
    assert "- something they wrote themselves" in got          # their bullet, in a section we own
    assert "- Cares about: the migration — source: a source" in got   # ours, appended after it


def test_the_tool_adds_its_sections_without_moving_anybodys(tmp_path):
    body = "\n# Jane Liu\n\n## My own notes\n\n- keep this first\n"
    p = write(tmp_path, body)
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    heads = [n for n, _ in E.parse_card(p.read_text().split("---\n", 2)[2]).blocks]
    assert "My own notes" in heads
    assert heads.index("Role and organisation") < heads.index("My own notes")   # kind sections lead
    assert heads[-1] == "Sources"                                              # tail last


def test_a_no_op_upsert_does_not_touch_the_file_at_all(tmp_path):
    """A page that is ALREADY a card. (A page with no sections at all is flat by definition, and
    the first touch imposes the shape — that is migration, and it is tested below.)"""
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    p = tmp_path / "kg/entities/person/jane-liu.md"
    before = p.read_text()
    r = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    assert r["changed"] is False
    assert p.read_text() == before


def test_a_page_with_no_sections_gets_the_card_on_its_first_touch(tmp_path):
    p = write(tmp_path, PROSE)
    r = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    assert r["migrated"] is True
    assert "## Role and organisation" in p.read_text()
    assert "She joined in March and runs the migration." in p.read_text()


def test_link_back_passes_through_a_strangers_page_without_marking_it(tmp_path):
    """`link_back` is the call that touches a page it was not asked to write. It adds one bullet."""
    p = write(tmp_path, FENCED)
    E.upsert_entity(tmp_path, "company", "Acme", [], "s", fields={"people": "[[Jane Liu]]"})
    got = p.read_text()
    assert "```markdown\n## Decided" in got and "That is the whole convention." in got
    assert "- [[Acme]] — works at" in got


def test_a_tail_section_created_by_link_back_lands_in_canonical_order(tmp_path):
    """`Card.add` used to APPEND a missing section, so a reciprocal chip written onto a page whose
    `## Connected` did not exist yet put it after `## Sources`. A tail section knows where it goes."""
    E.upsert_entity(tmp_path, "company", "Anthropic", [], "the web", fields={"what": "a lab"})
    E.upsert_entity(tmp_path, "person", "Dario Amodei", [], "meeting 105",
                    fields={"company": "[[Anthropic]]"})
    body = (tmp_path / "kg/entities/company/anthropic.md").read_text().split("---\n", 2)[2]
    heads = [n for n, _ in E.parse_card(body).blocks]
    assert heads.index("Connected") < heads.index("Sources")


def test_a_bullet_never_welds_the_next_heading_onto_its_section(tmp_path):
    """A section owns its own trailing blank line. Without that, appending the first bullet to
    `## Role and organisation` left `## What they care about` on the line right after it."""
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s",
                    fields={"role": "founder", "cares_about": "the migration"})
    got = (tmp_path / "kg/entities/person/jane-liu.md").read_text()
    assert "- Role: founder — source: s\n\n## What they care about" in got


# ── migration still happens ──────────────────────────────────────────────────────────────────────

def test_the_flat_page_still_migrates_on_touch(tmp_path):
    p = write(tmp_path, "\n# Jane Liu\n\n## 2026-09-01\n\n- a fact — source: mail\n")
    r = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    assert r["migrated"] is True
    got = p.read_text()
    assert "## Timeline" in got and "### 2026-09-01" in got
    assert "- a fact — source: mail" in got.split("## Timeline")[1]
    assert "\n## 2026-09-01\n" not in got


def test_migration_leaves_the_prose_around_it_alone(tmp_path):
    p = write(tmp_path, "\n# Jane Liu\n\nOne paragraph,\ntwo lines.\n\n## 2026-09-01\n\n- a fact\n")
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", fields={"role": "x"})
    assert "One paragraph,\ntwo lines." in p.read_text()


# ── the property ─────────────────────────────────────────────────────────────────────────────────

_PIECES = [
    lambda r: "",
    lambda r: "   ",
    lambda r: "Some prose on one line.",
    lambda r: "Prose with  double  spaces and a trailing space ",
    lambda r: "  two-space indented line",
    lambda r: "\tTabbed line",
    lambda r: f"# A title {r.randint(0, 9)}",
    lambda r: f"## {r.choice(['Connected', 'Sources', 'My notes', 'Timeline', '2026-09-01'])}",
    lambda r: f"### {r.choice(['2026-09-01', 'a sub head'])}",
    lambda r: f"- a bullet {r.randint(0, 9)}",
    lambda r: "* a star bullet",
    lambda r: "> a quote",
    lambda r: "```",
    lambda r: "```python",
    lambda r: "~~~",
    lambda r: "<!--",
    lambda r: "-->",
    lambda r: "<!-- a one-line comment -->",
    lambda r: "| a | table |",
    lambda r: "---",
    lambda r: "[[A Wikilink]] in prose",
]


def _random_markdown(r: random.Random) -> str:
    text = "\n".join(r.choice(_PIECES)(r) for _ in range(r.randint(0, 30)))
    return text + ("\n" if r.random() < 0.5 else "")


@pytest.mark.parametrize("seed", range(300))
def test_render_of_parse_is_the_identity_on_arbitrary_markdown(seed):
    """The whole guarantee, as a property: whatever the page is, reading and writing it back changes
    nothing. Unbalanced fences, stray `-->`, tabs, trailing spaces, a missing final newline — all of
    it comes back. Anything this tool then changes, it changed on purpose."""
    r = random.Random(seed)
    body = _random_markdown(r)
    assert E.render_card(E.parse_card(body)) == body


@pytest.mark.parametrize("seed", range(60))
def test_the_identity_survives_the_frontmatter_round_trip(seed):
    r = random.Random(1000 + seed)
    raw = "---\ntype: person\nid: x\ntitle: X\n---\n" + _random_markdown(r)
    fm, body = E.split_frontmatter(raw)
    assert E.render_card(E.parse_card(body), "person", fm) == raw
