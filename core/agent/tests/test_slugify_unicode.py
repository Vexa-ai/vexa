"""`slugify` gives every non-empty name a slug, whatever its script (#1783).

Live friction: `entity_upsert` refused a person whose name is written in Cyrillic with "an entity
needs a name that survives slugification", and a connection naming the same person was refused too.
The slug was ASCII-folded with NFKD, which has nothing to fold a Cyrillic, Greek or CJK letter to, so
the whole name vanished.

Cyrillic and Greek are now transliterated by a small fixed table; a name in a script with no table
(CJK, Arabic, Hebrew, …) that would otherwise leave nothing gets a stable hash slug. A name that was
already writable keeps EXACTLY the slug it had, so every existing page keeps its path — pinned below
against a frozen copy of the previous rule.

The terminal mirrors this in `clients/terminal/src/ui-kit/docLinks.tsx` (`entitySlug`); the same
vectors are asserted there, and `scripts/parity.json` holds the shared table to one spelling.
"""
from __future__ import annotations

import re
import unicodedata

import pytest

from workspaces.shared import entities as E


def _legacy_slugify(name: str) -> str:
    """The rule before #1783, frozen verbatim."""
    raw = str(name or "").replace("'", "").replace("’", "")
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80]


LATIN = [
    "James Hollister", "Meeting 96088138284", "mühlemann+popp AG", "Keith O'Donnell",
    "Keith O’Donnell", "Zürich Café", "Brain Trust", "Ångström Labs", "Łódź Ventures", "Søren Kierkegaard",
    "İstanbul Office", "Straße", "François-Xavier Dupont", "ACME, Inc.", "  spaced   out  ",
    "Ⅻ Roman", "ﬁnance", "x" * 120, "Nguyễn Thị Minh Khai", "Dvořák & Sons",
]


@pytest.mark.parametrize("name", LATIN)
def test_a_latin_or_ascii_name_keeps_the_slug_it_always_had(name):
    assert E.slugify(name) == _legacy_slugify(name)


@pytest.mark.parametrize("name,slug", [
    ("Дмитрий Гранкин", "dmitrii-grankin"),
    ("Щукин Юлия", "shchukin-iuliia"),
    ("Ёлка Объект", "elka-obieekt"),
    ("Київ Ґанок", "kiiv-ganok"),
    ("Ελένη Παπαδοπούλου", "eleni-papadopoulou"),
    ("Θεσσαλονίκη", "thessaloniki"),
    ("ООО Ромашка LLC", "ooo-romashka-llc"),
])
def test_cyrillic_and_greek_are_transliterated(name, slug):
    assert E.slugify(name) == slug


@pytest.mark.parametrize("name,slug", [
    ("東京", "u-68dea76f"),
    ("محمد", "u-e375c9cf"),
    ("  東京  ", "u-68dea76f"),
])
def test_a_script_with_no_table_gets_a_stable_hash_slug(name, slug):
    assert E.slugify(name) == slug


def test_two_different_unscripted_names_do_not_share_a_slug():
    assert E.slugify("東京") != E.slugify("大阪")


@pytest.mark.parametrize("name", ["", "   ", "'", "---", "!!!"])
def test_a_name_with_no_letter_or_digit_still_has_no_slug(name):
    assert E.slugify(name) == ""


def test_entity_upsert_accepts_a_cyrillic_name_and_a_cyrillic_connection(tmp_path):
    r = E.upsert_entity(tmp_path, "person", "Дмитрий Гранкин", ["Runs the weekly call."],
                        "the 2026-10-13 call", connections=["Ελένη Παπαδοπούλου"])
    assert r["path"] == "kg/entities/person/dmitrii-grankin.md", r
    page = (tmp_path / r["path"]).read_text()
    assert "title: Дмитрий Гранкин" in page
    assert "id: dmitrii-grankin" in page


def test_entity_upsert_accepts_a_cjk_name(tmp_path):
    r = E.upsert_entity(tmp_path, "company", "東京", ["Opened an office."], "the 2026-10-13 call")
    assert r["path"] == "kg/entities/company/u-68dea76f.md", r


def test_the_missing_source_refusal_shows_what_a_source_looks_like(tmp_path):
    with pytest.raises(E.EntityRefused) as exc:
        E.upsert_entity(tmp_path, "person", "Ada Lovelace", ["Wrote the first program."], "")
    msg = str(exc.value)
    assert "every fact needs a source" in msg
    assert 'source="' in msg, "the refusal should show an example source= value"


def test_a_mixed_script_page_written_under_its_latin_part_keeps_its_path(tmp_path):
    """Before #1783 "ООО Ромашка LLC" slugged to `llc`. Its page stays where it is."""
    company = tmp_path / "kg" / "entities" / "company"
    company.mkdir(parents=True)
    (company / "llc.md").write_text(
        "---\ntype: company\nid: llc\ntitle: ООО Ромашка LLC\n---\n# ООО Ромашка LLC\n")
    r = E.upsert_entity(tmp_path, "company", "ООО Ромашка LLC", ["Signed the pilot."], "the call")
    assert r["path"] == "kg/entities/company/llc.md", r
    assert r["created"] is False, r
    r2 = E.upsert_entity(tmp_path, "company", "ООО Лютик LLC", ["Asked for a demo."], "the call")
    assert r2["path"] == "kg/entities/company/ooo-liutik-llc.md", "another subject's page was reused"
