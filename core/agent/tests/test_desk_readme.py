"""The desk README as the desk: generated sections that are a VIEW over kg/, and never more.

PRD decision 26.4. The two claims that matter, and the second one matters more:

  1. the sections say what is on the desk, derived from `kg/` on every run;
  2. **nothing outside the markers is ever touched** — not the header the agent wrote, not a
     paragraph the person typed, not a section somebody added by hand.
"""
from __future__ import annotations

from shared import desk_readme
from shared.entities import upsert_entity


def _desk(tmp_path):
    d = tmp_path / "desk"
    (d / "kg" / "entities").mkdir(parents=True)
    return d


def _sections(text: str) -> dict:
    out = {}
    for key, _ in desk_readme.SECTIONS:
        s, e = f"<!-- desk:{key}:start -->", f"<!-- desk:{key}:end -->"
        i, j = text.find(s), text.find(e)
        out[key] = text[i + len(s): j] if i != -1 and j != -1 else None
    return out


def test_a_desk_with_no_readme_gets_one_with_every_section(tmp_path):
    d = _desk(tmp_path)
    out = desk_readme.update_readme(d)
    assert out["changed"] is True
    text = (d / "README.md").read_text()
    got = _sections(text)
    assert all(got[k] is not None for k, _ in desk_readme.SECTIONS)
    # empty is SAID, never omitted — an absent section reads as "not looked at"
    assert "No people on this desk yet" in got["people"]
    assert "belongs to no group workspace yet" in got["workspaces"]


def test_sections_list_what_kg_holds(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends the TSC."], "the 2026-03-02 meeting")
    upsert_entity(d, "company", "Sony Pictures Imageworks", ["Olga's employer."], "the meeting")
    upsert_entity(d, "meeting", "DNA TSC 2026-03-02", ["Kickoff."], "the transcript")
    desk_readme.update_readme(d)
    got = _sections((d / "README.md").read_text())
    assert "- [[Olga Avramenko]]" in got["people"]
    assert "- [[Sony Pictures Imageworks]]" in got["companies"]
    assert "- [[DNA TSC 2026-03-02]]" in got["meetings"]


def test_open_commitments_come_from_the_commitment_headings(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "meeting", "DNA TSC 2026-03-02", ["Kickoff."], "the transcript")
    page = d / "kg/entities/meeting/dna-tsc-2026-03-02.md"
    page.write_text(page.read_text() + "\n## Committed\n\n- Complete SSO onboarding\n- Circulate the charter\n")
    desk_readme.update_readme(d)
    got = _sections((d / "README.md").read_text())
    assert "- Complete SSO onboarding — [[DNA TSC 2026-03-02]]" in got["commitments"]
    assert "- Circulate the charter — [[DNA TSC 2026-03-02]]" in got["commitments"]


def test_next_dates_are_the_future_ones_only(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "meeting", "Next TSC", ["Scheduled for 2026-10-01."], "the invite",
                  today="2026-09-02")
    upsert_entity(d, "meeting", "Old TSC", ["Held on 2026-01-05."], "the transcript",
                  today="2026-09-02")
    desk_readme.update_readme(d, today="2026-09-02")
    got = _sections((d / "README.md").read_text())
    assert "2026-10-01" in got["dates"] and "2026-01-05" not in got["dates"]


def test_group_workspaces_are_listed_by_id_link(tmp_path):
    """A rename must not break the door — so the link is an id, never the group's name."""
    d = _desk(tmp_path)
    desk_readme.update_readme(d, workspaces=[{"id": "bbbbbbbbbb", "name": "ASWF DNA Project"}])
    got = _sections((d / "README.md").read_text())
    assert "- [[ws:bbbbbbbbbb/README.md]]" in got["workspaces"]
    assert "ASWF DNA Project" not in got["workspaces"]      # the name is resolved at read time


def test_text_outside_the_markers_is_never_touched(tmp_path):
    d = _desk(tmp_path)
    header = ("# Olga's desk\n\nWhat I actually care about this quarter is the DNA charter.\n\n"
              "## My own section\n\n- something I typed by hand\n\n")
    (d / "README.md").write_text(header)
    desk_readme.update_readme(d)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting")
    desk_readme.update_readme(d)
    text = (d / "README.md").read_text()
    assert text.startswith(header.rstrip("\n"))
    assert "- something I typed by hand" in text
    assert "[[Olga Avramenko]]" in text


def test_regeneration_replaces_only_between_the_markers(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting")
    desk_readme.update_readme(d)
    before = (d / "README.md").read_text()
    upsert_entity(d, "person", "Cottalango Leon", ["Chairs."], "the meeting")
    desk_readme.update_readme(d)
    after = (d / "README.md").read_text()
    assert before.count("<!-- desk:people:start -->") == after.count("<!-- desk:people:start -->") == 1
    assert "[[Cottalango Leon]]" in after and "[[Olga Avramenko]]" in after


def test_it_is_idempotent(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting")
    assert desk_readme.update_readme(d, today="2026-09-02")["changed"] is True
    assert desk_readme.update_readme(d, today="2026-09-02")["changed"] is False


def test_a_long_desk_is_capped_and_says_so(tmp_path):
    d = _desk(tmp_path)
    for i in range(desk_readme.MAX_ROWS + 7):
        upsert_entity(d, "person", f"Person Number{i:03d}", ["Attends."], "the meeting")
    desk_readme.update_readme(d)
    got = _sections((d / "README.md").read_text())
    assert got["people"].count("\n- ") == desk_readme.MAX_ROWS
    assert "7 more" in got["people"]
