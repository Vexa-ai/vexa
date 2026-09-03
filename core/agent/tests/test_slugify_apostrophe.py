"""`slugify` — one rule for apostrophes, everywhere (ledger F200, live agent, 2026-09-03).

Live repro: `kg/entities/person/keith-odonnell.md` already existed (a human/agent-typed filename
via `workspace_write`, which naturally dropped the apostrophe). The write-back phase then called
`entity_upsert(person, "Keith O'Donnell", ...)`, and `slugify` turned the apostrophe into a hyphen
like any other punctuation — producing `keith-o-donnell.md`, a second page for the same person.

`upsert_entity`'s own create-vs-append logic already does "look up before creating" correctly: it
resolves the target path from the slug and appends if that path exists. The bug was never a missing
lookup — it was that the SAME name produced two different paths depending on who typed the slug.
"""
from __future__ import annotations

from shared import entities as E


def test_a_straight_apostrophe_is_dropped_not_hyphenated():
    assert E.slugify("Keith O'Donnell") == "keith-odonnell"


def test_a_curly_apostrophe_is_dropped_not_hyphenated():
    assert E.slugify("Keith O’Donnell") == "keith-odonnell"


def test_a_real_space_is_still_a_hyphen():
    """The fix narrows to apostrophes specifically — every other separator still becomes one
    hyphen, so a name is never assembled by two different rules depending on which character it
    hits first."""
    assert E.slugify("Brain Trust") == "brain-trust"


def test_upsert_entity_lands_on_the_existing_page_once_the_slugs_agree(tmp_path):
    person = tmp_path / "kg" / "entities" / "person"
    person.mkdir(parents=True)
    (person / "keith-odonnell.md").write_text(
        "---\ntype: person\nid: keith-odonnell\ntitle: Keith O'Donnell\n---\n"
        "# Keith O'Donnell\n\nFINOS.\n")
    r = E.upsert_entity(tmp_path, "person", "Keith O'Donnell",
                        ["Chairs the Zenith SIG call."], "the 2026-09-03 call")
    assert r["path"] == "kg/entities/person/keith-odonnell.md", r
    assert r["created"] is False, r
    assert not (person / "keith-o-donnell.md").exists(), "created a second page for the same person"
