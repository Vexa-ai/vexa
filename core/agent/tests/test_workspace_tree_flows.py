"""`workspace_tree` ON `_global` VS ON A DESK — what the AGENT is shown of `flows/`.

Founder, 2026-09-06: *"flows live in global, right?"* (Vexa-ai/vexa#1626). The terminal's hide list
(`clients/terminal/src/minutes/machinery.ts`) treated `flows/` as machinery in every workspace, so
the generated flow pages (#1615) were reachable by link and by nothing else. That is now per
workspace: content in `_global`, machinery on a desk and in a group.

THE SERVER SIDE NEVER HID `flows/` AT ALL, and this file is what says so out loud. `tree_at` — the
single enumerator behind the Files tree, the MCP `workspace_tree`, the link resolver and the
find-file index — hides `.git`, dotfiles, `kg/templates/` and anything flagged `template: true`.
`flows/` is not on that list and never was. So the half of #1626 the agent needs is already true
(`_global/flows/*.md` enumerates), and the other half is NOT introduced here on purpose:

  hiding `flows/` from a desk's `workspace_tree` would take `flows/personal.md`, `shared.md` and
  `global.md` off the list — the three files that desk's own `CLAUDE.md` points at by name. The
  issue asks to SHOW the company layer's pages, not to stop showing the desk's playbooks, and a
  restriction nobody asked for is not a side effect to ship quietly. If the agent should stop
  seeing them, that is a founder's call and a separate change.

So: pinned as it stands, in both workspaces, with the reason written down.
"""
from __future__ import annotations

import pathlib

from control_plane import global_seed
from control_plane.workspace_reader import WorkspaceReader

REPO = pathlib.Path(__file__).resolve().parents[3]
SEED_IN_REPO = REPO / "behavior" / "global"
DESK_SEED_IN_REPO = REPO / "behavior" / "workspaces" / "default"


def _reader(tmp_path) -> WorkspaceReader:
    return WorkspaceReader(str(tmp_path))


def test_the_company_layer_lists_its_generated_flow_pages(tmp_path):
    """The real seed, copied in the way the instance copies it, then enumerated the way the MCP
    tool enumerates it. This is the deliverable of #1626 on the agent's side."""
    root = tmp_path / "_global"
    root.mkdir()
    global_seed.top_up(root, [(SEED_IN_REPO, "")])

    files = _reader(tmp_path).tree_at(root)

    assert "flows/README.md" in files, "the flow index did not reach _global's listing"
    pages = [f for f in files if f.startswith("flows/") and f != "flows/README.md"]
    assert pages, "no generated flow page is listed in _global — the admin cannot browse to one"
    assert "POLICIES.md" in files and "README.md" in files


def test_a_desk_lists_its_own_playbooks_too_because_this_reader_hides_neither(tmp_path):
    """`flows/` is machinery in the TERMINAL's listing of a desk, not in this enumerator. Written
    as an assertion rather than as a comment so that adding a hide here has to face this test."""
    desk = tmp_path / "u_jane"
    (desk / "flows").mkdir(parents=True)
    (desk / "flows" / "personal.md").write_text("# personal\n")
    (desk / "kg").mkdir()
    (desk / "kg" / "note.md").write_text("body\n")

    files = _reader(tmp_path).tree("u_jane")

    assert files == ["flows/personal.md", "kg/note.md"]


def test_the_desk_seed_really_does_carry_the_playbooks_the_agent_is_told_to_read(tmp_path):
    """The reason the test above is a pin and not an oversight: `CLAUDE.md` names these three."""
    claude = (DESK_SEED_IN_REPO / "CLAUDE.md").read_text(encoding="utf-8")
    for name in ("flows/personal.md", "flows/shared.md", "flows/global.md"):
        assert (DESK_SEED_IN_REPO / name).is_file(), f"{name} is missing from the desk seed"
        assert name in claude, f"{name} is no longer named in the desk's CLAUDE.md"


def test_what_this_enumerator_does_hide_is_unchanged_in_both(tmp_path):
    """The shapes and the plumbing, in `_global` exactly as on a desk."""
    reader = _reader(tmp_path)
    for name in ("_global", "u_jane"):
        ws = tmp_path / name
        (ws / "kg" / "templates").mkdir(parents=True)
        (ws / "kg" / "templates" / "person.md").write_text("---\ntype: person\n---\n<Full Name>\n")
        (ws / "kg" / "note.md").write_text("body\n")
        (ws / "flows").mkdir()
        (ws / "flows" / "post_meeting.md").write_text("---\nkind: flow\n---\n")
        (ws / ".git").mkdir()
        (ws / ".git" / "HEAD").write_text("ref\n")
        (ws / ".env").write_text("SECRET=1\n")

        files = reader.tree_at(ws)

        assert "kg/templates/person.md" not in files
        assert not any(f.startswith(".git") or f == ".env" for f in files)
        assert files == ["flows/post_meeting.md", "kg/note.md"]
