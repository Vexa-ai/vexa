"""F42 — what a person sees when they sign in with no link.

The founder signed in as a new user and got: a seeded "Personal" chat on the generic greeting, an
admin-only "Organisation setup" card, and his desk's README TEMPLATE rendered as a page —
"(unset) — this workspace has not been set up yet … Purpose (unset) … Objective (unset)".
"i logged as new user, that's what i see - not happy about that."
"""
from __future__ import annotations

from pathlib import Path

from control_plane.scaffolds import KINDS, facts_block
from shared.seeding import seed_workspace


# ── (b) a new desk starts with no template pages ────────────────────────────────────────────────

def _seed(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    (seed / "kg" / "entities" / "person").mkdir(parents=True)
    (seed / "kg" / "templates").mkdir(parents=True)
    (seed / "flows").mkdir()
    (seed / "README.md").write_text("# (unset) — this workspace has not been set up yet\n")
    (seed / "CLAUDE.md").write_text("# conventions\n")
    (seed / "kg" / "index.md").write_text("# index\n")
    (seed / "kg" / "entities" / "index.md").write_text("# entities\n")
    (seed / "kg" / "entities" / "person" / "index.md").write_text("# people\n")
    (seed / "kg" / "templates" / "person.md").write_text("---\ntemplate: true\n---\n")
    (seed / "flows" / "personal.md").write_text("# flow\n")
    return seed


def test_the_unset_readme_is_never_copied_into_a_desk(tmp_path):
    ws = seed_workspace(tmp_path / "desk", _seed(tmp_path))
    assert not (ws / "README.md").exists()
    # the exact string he was shown must not be anywhere in the desk
    for f in ws.rglob("*.md"):
        assert "this workspace has not been set up yet" not in f.read_text()


def test_index_pages_are_scaffolding_for_a_graph_with_nothing_in_it(tmp_path):
    ws = seed_workspace(tmp_path / "desk", _seed(tmp_path))
    assert not (ws / "kg" / "index.md").exists()
    assert not (ws / "kg" / "entities" / "index.md").exists()
    assert not (ws / "kg" / "entities" / "person" / "index.md").exists()
    # …but the directory the agent writes entities into EXISTS and is empty. A missing directory is
    # a different failure from an empty one.
    assert (ws / "kg" / "entities").is_dir()
    assert not any((ws / "kg" / "entities").rglob("*.md"))


def test_machinery_survives_because_it_is_not_content(tmp_path):
    # CLAUDE.md, flows/ and kg/templates/ are read BY the agent; a person never opens them as a
    # page. Dropping them would change how every desk behaves — a bigger change than the defect.
    ws = seed_workspace(tmp_path / "desk", _seed(tmp_path))
    assert (ws / "CLAUDE.md").exists()
    assert (ws / "flows" / "personal.md").exists()
    assert (ws / "kg" / "templates" / "person.md").exists()
    assert (ws / ".git").exists()


def test_seeding_is_still_idempotent(tmp_path):
    seed = _seed(tmp_path)
    ws = seed_workspace(tmp_path / "desk", seed)
    (ws / "kg" / "entities" / "mine.md").write_text("written by the agent\n")
    again = seed_workspace(tmp_path / "desk", seed)
    assert again == ws
    assert (ws / "kg" / "entities" / "mine.md").exists()   # an existing desk is returned untouched


# ── (a) a first visit knows what the company already involves them in ───────────────────────────

def test_first_visit_is_a_kind():
    assert "first-visit" in KINDS


def test_the_facts_block_names_the_shared_workspace_and_the_invited_meeting():
    block = facts_block({
        "kind": "first-visit", "workspaces": ["_global", "127"],
        "refs": {
            "who": "dmitry@vexa.ai", "domain": "vexa.ai",
            "shared_workspaces": [{"slug": "aswf-dna-b7b2", "name": "ASWF DNA",
                                   "purpose": "everything about the DNA project"}],
            "invited_meetings": [{"meeting": "31", "title": "DNA TSC", "when": "Thu 14:00"}],
            "state": {"desk": "new", "group": "absent"},
        },
    })
    assert "ASWF DNA — everything about the DNA project" in block
    assert "DNA TSC at Thu 14:00" in block


def test_empty_is_SAID_not_omitted():
    # "nothing is shared with you yet" is a sentence the preset says out loud, so the line must be
    # present and empty rather than absent — absent reads as "not looked up".
    block = facts_block({"kind": "first-visit", "workspaces": ["_global"],
                         "refs": {"who": "a@b.io", "shared_workspaces": [],
                                  "invited_meetings": []}})
    assert "shared with them: nothing yet" in block
    assert "meetings they are invited to: none yet" in block


def test_a_lookup_that_FAILED_says_nothing_rather_than_asserting_emptiness():
    # The caller omits the key when it could not answer. Emptiness and ignorance are different
    # facts and the person is told a different sentence for each.
    block = facts_block({"kind": "first-visit", "workspaces": ["_global"], "refs": {"who": "a@b.io"}})
    assert "shared with them" not in block
    assert "meetings they are invited to" not in block
