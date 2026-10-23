"""A link field written as a sentence, and what the answer says about templates (Vexa-ai/vexa#1783).

Two things an agent met on 2026-10-13 writing pages back:

* `entity_upsert(kind="project", fields={"who": "Alex Example (GitHub handle); maintained by
  Dmitry Grankin"})` was refused `400 Connection name must identify one page` — `who` links the
  pages it names, and the whole sentence was taken as ONE name. A field is prose; the names in it
  are the links. The line is filed as written and each name in it gets its chip.
* a person page answered `"template": "company"`, which read as "built from the company kind's
  template". It meant the ORGANISATION's template (`_global/kg/templates/<kind>.md`); the answer now
  names that file, and says nothing when the baked shape was used.
"""
from __future__ import annotations

from workspaces.shared import entities as E


def _connected(ws, rel):
    text = (ws / rel).read_text()
    block = text.split("## Connected", 1)[1].split("\n## ", 1)[0]
    return [l.strip() for l in block.splitlines() if l.strip().startswith("- [[")]


def test_a_who_sentence_is_filed_as_written_and_links_each_name(tmp_path):
    r = E.upsert_entity(tmp_path, "project", "Voice Agent Integration", [], "the PR",
                        fields={"who": "Alex Example (GitHub alexexample); maintained by Dmitry Grankin"})
    text = (tmp_path / r["path"]).read_text()
    assert "Alex Example (GitHub alexexample); maintained by Dmitry Grankin" in text
    chips = _connected(tmp_path, r["path"])
    assert any("[[Alex Example]]" in c for c in chips), chips
    assert any("[[Dmitry Grankin]]" in c for c in chips), chips


def test_a_list_of_people_in_one_field_links_each(tmp_path):
    r = E.upsert_entity(tmp_path, "meeting", "Weekly sync", [], "the call",
                        fields={"who": "Ada Lovelace, Grace Hopper and Alan Turing"})
    chips = " ".join(_connected(tmp_path, r["path"]))
    for name in ("Ada Lovelace", "Grace Hopper", "Alan Turing"):
        assert f"[[{name}]]" in chips


def test_a_field_with_no_name_in_it_is_filed_without_a_link(tmp_path):
    r = E.upsert_entity(tmp_path, "meeting", "Weekly sync", [], "the call",
                        fields={"who": "everyone from the platform team"})
    text = (tmp_path / r["path"]).read_text()
    assert "everyone from the platform team" in text
    assert "[[everyone from the platform team]]" not in text


def test_wikilinks_in_a_field_still_decide_the_links(tmp_path):
    r = E.upsert_entity(tmp_path, "project", "Launch", [], "the call",
                        fields={"who": "[[Ada Lovelace]] leads; [[Grace Hopper]] reviews"})
    chips = " ".join(_connected(tmp_path, r["path"]))
    assert "[[Ada Lovelace]]" in chips and "[[Grace Hopper]]" in chips


def test_the_baked_shape_reports_no_template(tmp_path):
    r = E.upsert_entity(tmp_path, "person", "Ada Lovelace", ["Wrote the first program."], "a book")
    assert r.get("template") in (None, "")


def test_an_organisation_template_is_reported_by_its_file(tmp_path):
    shape = "# {{title}}\n\n## Role and organisation\n\n## Risk\n\n## Connected\n"
    r = E.upsert_entity(tmp_path, "person", "Ada Lovelace", ["Wrote the first program."], "a book",
                        template=shape)
    if "Risk" in r["sections"]:
        assert r["template"] == "_global/kg/templates/person.md"
