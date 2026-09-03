"""candidate_names / missing_names — the write-back phase's pre-pass, proved against the exact
fragments the live agent measured (ledger, 2026-09-03, F202-F205).

No model, no HTTP: `candidate_names` is a pure regex over text, and `missing_names` is that regex
plus a directory listing. Both are provable offline, which is the whole point of the pre-pass
existing as code rather than a model call (see `entities.py`'s own module docstring for the phase).
"""
from __future__ import annotations

from shared import entities as E


# ── F202/F203 — skill boilerplate is not a name ────────────────────────────────────────────────────

def test_the_extend_preset_imperative_is_not_a_name():
    """Live repro: the write-back phase after an `/extend` turn asked to create pages for "Then
    WRITE IT" and "Say ONE" — verbatim fragments of `behavior/asks/extend.md`'s own instruction
    text, which IS the turn's prompt for that intent (chat_intents.py), not anything a person or
    the model said about an entity."""
    text = ("Then WRITE IT, at that exact path. Match the shape of its neighbours: where the "
            "workspace files this kind of thing with frontmatter and a Decided/Open split, so "
            "does this one.\n\n"
            "Say ONE line naming what you made and what is thin about it. Not a summary of the "
            "page — they are about to read it.")
    out = E.candidate_names(text, mask_linked=False)
    assert "Then WRITE IT" not in out, out
    assert "Say ONE" not in out, out


def test_the_create_preset_imperative_is_not_a_name():
    text = ("Find out what belongs there BEFORE you write a word of it.\n\n"
            "Then WRITE IT, at that exact path.")
    assert "Then WRITE IT" not in E.candidate_names(text, mask_linked=False)


# ── F205 — a quoted title is not a name the turn is introducing ───────────────────────────────────

def test_a_quoted_web_page_title_is_not_a_name():
    """Live repro: a tool result surfaced finos.org's "This Week At FINOS: Week Of August 17,
    2026" newsletter page, and the model's own prose quoted that headline back. The extractor
    read the quoted fragment "Week Of August" as a name with no page."""
    text = ('I found finos.org\'s "This Week At FINOS: Week Of August 17, 2026" newsletter page, '
            "which does not mention Zenith SIG.")
    out = E.candidate_names(text, mask_linked=False)
    assert "Week Of August" not in out, out
    assert "This Week At FINOS" not in out, out
    # ...and an unquoted real name in the same sentence still comes through.
    assert "Zenith SIG" in out, out


def test_curly_quotes_are_masked_too():
    text = "The page is titled “Week Of August 17” in the newsletter."
    assert "Week Of August" not in E.candidate_names(text, mask_linked=False)


# ── a name does not survive a line break ───────────────────────────────────────────────────────────

def test_a_name_cannot_be_assembled_across_a_line_break():
    """Two unrelated capitalised words that happen to be adjacent only because one ends a line and
    the next starts one must not be read as a single two-word name."""
    text = "Reported by Peter Smulovics.\nZenith SIG did not meet today."
    out = E.candidate_names(text, mask_linked=False)
    assert "Smulovics Zenith" not in out, out
    assert "Peter Smulovics" in out, out
    assert "Zenith SIG" in out, out


# ── F204 — a fragment of an existing page's title is not a new name ───────────────────────────────

def test_missing_names_drops_a_fragment_of_an_existing_page(tmp_path):
    """Live repro: the write-back phase asked to create "Zenith SI" — a truncated echo of "Zenith
    SIG", which already has a page. An exact-slug check does not catch this (the fragment's slug,
    "zenith-si", is not the real page's slug, "zenith-sig") — the fix is a prefix test against
    every page the desk already has, the same trade `_drop_prefixes` already makes within one
    batch, extended to the desk's existing pages."""
    proj = tmp_path / "kg" / "entities" / "project"
    proj.mkdir(parents=True)
    (proj / "zenith-sig.md").write_text("---\ntype: project\nid: zenith-sig\n---\n# Zenith SIG\n")
    out = E.missing_names([tmp_path], ["Zenith SI did not surface a participant roster today."])
    assert out == [], out


def test_missing_names_still_offers_a_genuinely_new_name(tmp_path):
    proj = tmp_path / "kg" / "entities" / "project"
    proj.mkdir(parents=True)
    (proj / "zenith-sig.md").write_text("---\ntype: project\nid: zenith-sig\n---\n# Zenith SIG\n")
    out = E.missing_names([tmp_path], ["Brain Trust met to discuss the roadmap."])
    assert out == ["Brain Trust"], out
