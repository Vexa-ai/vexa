"""candidate_names / missing_names — the write-back phase's pre-pass, proved against the exact
fragments the live agent measured (ledger, 2026-09-03, F202-F205).

No model, no HTTP: `candidate_names` is a pure regex over text, and `missing_names` is that regex
plus a directory listing. Both are provable offline, which is the whole point of the pre-pass
existing as code rather than a model call (see `entities.py`'s own module docstring for the phase).
"""
from __future__ import annotations

from workspaces.shared import entities as E


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


# ── 2026-09-06 · Vexa-ai/vexa#1620 — one test per friction report, on its own literal input ───────


def test_an_option_line_is_not_a_name(tmp_path):
    """fr_e37676879d4c1ec7 (13:30Z, global setup): after writing `policy/visibility.md` with three
    multiple-choice options the phase asked for a page called "Any Vexa" — the head of the option
    line *"Any Vexa meeting on the org"*. An option is a phrase; "Any" is capitalised because the
    line starts there. The part that IS a name, "Vexa", already had pages."""
    text = ("Which meetings are visible to the whole org by default?\n\n"
            "1. Any Vexa meeting on the org\n"
            "2. Only meetings whose organiser shares them\n"
            "3. Nothing by default\n\n"
            "Martin Kocher asked for the second one.\n")
    out = E.candidate_names(text, mask_linked=False)
    assert "Any Vexa" not in out, out
    # ...and the name said in the same note still comes through, all the way to the phase's list.
    assert "Martin Kocher" in out, out
    assert E.missing_names([tmp_path], [text]) == ["Martin Kocher"]


def test_a_dropped_prefix_does_not_propose_a_second_page(tmp_path):
    """fr_e805ab2ab6675bff (13:37Z): the phase offered "NB Governing Board" while
    `kg/entities/project/oenb-governing-board.md` was already on the desk. The "Oe" was dropped
    upstream, and every check the dedup had — exact slug, then a prefix — compares from the LEFT,
    which is the end that was damaged. A near-duplicate page was one tool call away."""
    proj = tmp_path / "kg" / "entities" / "project"
    proj.mkdir(parents=True)
    (proj / "oenb-governing-board.md").write_text(
        "---\ntype: project\nid: oenb-governing-board\ntitle: OeNB Governing Board\n---\n"
        "# OeNB Governing Board\n")
    assert E.missing_names([tmp_path], ["NB Governing Board met on Thursday."]) == []
    # the spelling the page itself uses is subtracted the way it always was
    assert E.missing_names([tmp_path], ["OeNB Governing Board met on Thursday."]) == []
    # ...and a different project on the same desk is still offered
    assert E.missing_names([tmp_path], ["Vienna Data Board met on Thursday."]) == \
        ["Vienna Data Board"]


def test_the_dedup_reads_the_title_not_only_the_filename(tmp_path):
    """A filename is one spelling of a page, not the only one: the same desk, with the page filed
    under a different id and the title carrying the name, must still not be asked for twice."""
    proj = tmp_path / "kg" / "entities" / "project"
    proj.mkdir(parents=True)
    (proj / "board-2026.md").write_text(
        "---\ntype: project\nid: board-2026\ntitle: OeNB Governing Board\n---\n"
        "# OeNB Governing Board\n")
    assert E.missing_names([tmp_path], ["NB Governing Board met on Thursday."]) == []


def test_a_document_wikilink_is_a_reference_not_a_name(tmp_path):
    """fr_e96aa977edd14de8 (13:54Z): `structure.md` is the org-chart document the research job
    wrote, linked from every person page as `[[structure]]`, and the extractor surfaced "Structure"
    as a missing NAME — every wikilink was a person/company chip whatever it pointed at."""
    (tmp_path / "structure.md").write_text("# OeNB org chart\n")
    r = E.upsert_entity(tmp_path, "person", "Martin Kocher",
                        ["Sits at the top of [[structure]] with [[Josef Meichenitsch]]."],
                        "the OeNB research job")
    assert r["links_docs"] == ["structure"], r          # the document link is a reference
    assert r["links_resolved"] == ["structure"], r      # ...and it does point at a page that exists
    assert r["links_missing"] == ["Josef Meichenitsch"], r   # a NAME with no page still asks
    # and the phase's own pre-pass does not read the link as a name either
    assert E.missing_names([tmp_path], ["Sits at the top of [[structure]]."]) == []


def test_a_link_to_a_page_in_another_mounted_workspace_is_not_missing(tmp_path):
    """The other half of the same rule: a page the reader can already open is not missing wherever
    it lives. Neither workspace here has an identity file, so resolution cannot lean on the
    cross-workspace id rewrite having run first."""
    desk, group = tmp_path / "desk", tmp_path / "group"
    desk.mkdir()
    group.mkdir()
    E.upsert_entity(group, "project", "OeNB Governing Board", ["Meets monthly."], "the mail")
    out = E.upsert_entity(desk, "meeting", "OeNB check-in",
                          ["[[OeNB Governing Board]] was named."], "the transcript",
                          mounts=[{"path": str(desk)}, {"path": str(group)}])
    assert out["links_resolved"] == ["OeNB Governing Board"] and out["links_missing"] == []
