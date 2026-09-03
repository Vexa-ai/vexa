"""entity_upsert — the ONE write behind PRD decision 24, proved offline over a directory.

No docker, no HTTP, no model. The module is a pure function over a workspace path precisely so the
rules it enforces (a source or nothing; idempotent on identical facts; the existing frontmatter
vocabulary, not a new one) can be argued with in a test rather than observed in production.
"""
from __future__ import annotations

import subprocess

import pytest

from shared import entities as E


def read(ws, rel):
    return (ws / rel).read_text()


# ── creating a page ──────────────────────────────────────────────────────────────────────────────

def test_creates_the_page_with_the_readers_frontmatter_vocabulary(tmp_path):
    r = E.upsert_entity(tmp_path, "person", "Olga Avramenko",
                        ["Runs the DNA TSC agenda."], "the 2026-03-02 TSC call", today="2026-09-02")
    assert r["created"] is True and r["path"] == "kg/entities/person/olga-avramenko.md"
    text = read(tmp_path, r["path"])
    # type/id/title are what the templates, the per-type index.md files and the terminal's wikilink
    # resolver already read. A page keyed on `kind:`/`name:` would look right and be invisible.
    assert "type: person" in text
    assert "id: olga-avramenko" in text
    assert "title: Olga Avramenko" in text
    assert "created: 2026-09-02" in text
    assert "sources: [the 2026-03-02 TSC call]" in text
    assert "aliases: []" in text
    assert "## 2026-09-02" in text
    assert "- Runs the DNA TSC agenda. — source: the 2026-03-02 TSC call" in text


def test_every_kind_decision_24_names_is_writable_and_nothing_else_is(tmp_path):
    for kind in ("person", "company", "meeting", "project", "decision"):
        r = E.upsert_entity(tmp_path, kind, f"A {kind}", ["a fact"], "a source")
        assert r["path"].startswith(f"kg/entities/{kind}/")
    with pytest.raises(E.EntityRefused):
        E.upsert_entity(tmp_path, "vendor", "Acme", ["a fact"], "a source")


# ── the counter-rule: a page carries only what was said or read, with its source ─────────────────

def test_a_fact_with_no_source_is_refused_and_names_MISSING(tmp_path):
    with pytest.raises(E.EntityRefused) as e:
        E.upsert_entity(tmp_path, "company", "Sony Pictures Imageworks", ["2,000 people"], "  ")
    assert "kg/MISSING.md" in str(e.value)
    assert not (tmp_path / "kg" / "entities").exists()   # refused means NOTHING was written


def test_no_facts_is_refused_rather_than_writing_an_empty_dated_heading(tmp_path):
    with pytest.raises(E.EntityRefused):
        E.upsert_entity(tmp_path, "person", "Nobody", [], "a source")


# ── appending, and idempotency ───────────────────────────────────────────────────────────────────

def test_second_call_appends_a_dated_entry_and_keeps_the_first(tmp_path):
    E.upsert_entity(tmp_path, "person", "Cottalango Leon", ["Chairs the TSC."], "call A",
                    today="2026-03-02")
    r = E.upsert_entity(tmp_path, "person", "Cottalango Leon", ["Asked for a standard CLA."],
                        "call B", today="2026-08-18")
    assert r["created"] is False and r["changed"] is True and r["facts_written"] == 1
    text = read(tmp_path, r["path"])
    assert "Chairs the TSC." in text and "Asked for a standard CLA." in text
    assert "## 2026-03-02" in text and "## 2026-08-18" in text
    assert "sources: [call A, call B]" in text


def test_identical_facts_write_nothing_at_all(tmp_path):
    E.upsert_entity(tmp_path, "company", "Vexa", ["Ships a meeting bot."], "the README",
                    today="2026-09-01")
    before = read(tmp_path, "kg/entities/company/vexa.md")
    r = E.upsert_entity(tmp_path, "company", "Vexa", ["ships a meeting bot"], "the README again",
                        today="2026-09-02")
    # THIS is what makes a forced write-back phase on EVERY turn affordable: a turn that learned
    # nothing new costs one no-op, not a duplicated paragraph and not a second `sources` entry.
    assert r["changed"] is False and r["facts_written"] == 0 and r["already_recorded"] == 1
    assert read(tmp_path, "kg/entities/company/vexa.md") == before


def test_a_repeated_fact_is_dropped_and_the_new_one_kept(tmp_path):
    E.upsert_entity(tmp_path, "person", "Marvin", ["Works at OeNB."], "mail", today="2026-09-01")
    r = E.upsert_entity(tmp_path, "person", "Marvin", ["Works at OeNB.", "Asked for prod versions."],
                        "call", today="2026-09-02")
    assert r["facts_written"] == 1 and r["already_recorded"] == 1
    assert read(tmp_path, r["path"]).count("Works at OeNB.") == 1


def test_an_existing_hand_written_page_keeps_its_own_frontmatter(tmp_path):
    p = tmp_path / "kg/entities/person/jane-liu.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntype: person\nid: jane-liu\ntitle: Jane Liu\nself: true\n---\n\n# Jane Liu\n")
    E.upsert_entity(tmp_path, "person", "Jane Liu", ["Moved to Berlin."], "her message",
                    today="2026-09-02")
    text = p.read_text()
    assert "self: true" in text          # a key this module has never heard of survives
    assert "title: Jane Liu" in text     # and a title somebody else set is not rewritten
    assert "created: 2026-09-02" in text and "sources: [her message]" in text


# ── wikilinks ────────────────────────────────────────────────────────────────────────────────────

def test_wikilinks_resolve_and_the_missing_ones_come_back_as_the_next_calls(tmp_path):
    E.upsert_entity(tmp_path, "company", "Sony Pictures Imageworks", ["A VFX studio."], "the web")
    r = E.upsert_entity(tmp_path, "person", "Olga Avramenko",
                        ["Works at [[Sony Pictures Imageworks]] with [[Cottalango Leon]]."],
                        "the TSC call")
    assert r["links_resolved"] == ["Sony Pictures Imageworks"]
    # NOT auto-created: a page minted from a name with no facts behind it is the invention
    # decision 24.5 forbids. It is returned so the caller upserts it with its own source.
    assert r["links_missing"] == ["Cottalango Leon"]
    assert not (tmp_path / "kg/entities/person/cottalango-leon.md").exists()


# ── the index ────────────────────────────────────────────────────────────────────────────────────

def test_index_lists_kind_name_path_and_last_updated(tmp_path):
    E.upsert_entity(tmp_path, "person", "Olga Avramenko", ["a"], "s", today="2026-03-02")
    E.upsert_entity(tmp_path, "person", "Olga Avramenko", ["b"], "s", today="2026-08-03")
    E.upsert_entity(tmp_path, "company", "Vexa", ["c"], "s", today="2026-05-11")
    rel = E.write_index(tmp_path, "desk-1")
    text = read(tmp_path, rel)
    assert rel == "kg/INDEX.md"
    assert "| person | Olga Avramenko | `kg/entities/person/olga-avramenko.md` | 2026-08-03 |" in text
    assert "| company | Vexa | `kg/entities/company/vexa.md` | 2026-05-11 |" in text
    assert "desk-1" in text


def test_index_never_lists_a_template(tmp_path):
    p = tmp_path / "kg/entities/person/shape.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntemplate: true\ntype: person\ntitle: <Full Name>\n---\n")
    assert "<Full Name>" not in E.render_index(tmp_path)


def test_an_empty_workspace_says_so_instead_of_pretending(tmp_path):
    assert "No entity pages exist here yet" in E.render_index(tmp_path)


# ── the commit ───────────────────────────────────────────────────────────────────────────────────

def git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True).stdout.strip()


def repo(tmp_path):
    ws = tmp_path / "desk-7"
    ws.mkdir()
    git(ws, "init", "-q")
    git(ws, "config", "user.email", "t@t.t")
    git(ws, "config", "user.name", "t")
    (ws / "seed.md").write_text("x")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "seed")
    return ws


def test_one_commit_carries_the_F31_subject_shape(tmp_path):
    ws = repo(tmp_path)
    r = E.upsert_entity(ws, "person", "Olga Avramenko", ["a fact"], "a source")
    idx = E.write_index(ws, "desk-7")
    sha = E.commit_entity(ws, [r["path"], idx], subject_path=r["path"], created=True)
    assert sha
    assert git(ws, "log", "-1", "--format=%s") == \
        "desk-7: kg/entities/person/olga-avramenko.md — added"
    r2 = E.upsert_entity(ws, "person", "Olga Avramenko", ["another fact"], "a source")
    E.commit_entity(ws, [r2["path"], idx], subject_path=r2["path"], created=False)
    assert git(ws, "log", "-1", "--format=%s").endswith(" — updated")


def test_the_commit_is_by_pathspec_and_leaves_a_concurrent_writers_work_alone(tmp_path):
    """`git commit` commits THE INDEX. A bare add+commit here would sweep in whatever a worker turn
    running in the same repo had staged and file it under this subject — the standing rule, one
    level down: the git index is a write surface with no owner."""
    ws = repo(tmp_path)
    (ws / "someone-elses-draft.md").write_text("mid-turn work")
    git(ws, "add", "someone-elses-draft.md")            # staged by another writer
    r = E.upsert_entity(ws, "person", "Olga", ["a fact"], "a source")
    E.commit_entity(ws, [r["path"]], subject_path=r["path"], created=True)
    assert "someone-elses-draft.md" not in git(ws, "show", "--name-only", "--format=", "HEAD")
    assert "someone-elses-draft.md" in git(ws, "diff", "--cached", "--name-only")


def test_a_no_op_upsert_produces_no_commit(tmp_path):
    ws = repo(tmp_path)
    r = E.upsert_entity(ws, "person", "Olga", ["a fact"], "a source")
    E.commit_entity(ws, [r["path"]], subject_path=r["path"], created=True)
    head = git(ws, "rev-parse", "HEAD")
    again = E.upsert_entity(ws, "person", "Olga", ["a fact"], "a source")
    assert again["changed"] is False
    assert E.commit_entity(ws, [again["path"]], subject_path=again["path"], created=False) is None
    assert git(ws, "rev-parse", "HEAD") == head
