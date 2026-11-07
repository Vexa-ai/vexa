"""wpaths.read_text_inside / read_bytes_inside / write_text_inside / unlink_inside reach a FIXED
platform path inside a work tree without following a link at the file or at any directory above it.

The model's tools can write a work tree during a turn, so a planted symlink — at the leaf or at any
directory component — must never redirect a root read (its content would reach a caller or the model)
or a root write (a credential or an identity would land wherever the link points). These helpers open
each directory component O_NOFOLLOW, read only a regular single-link file, and write a new file renamed
into place.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from workspaces.shared import workspace_paths as wp


@pytest.fixture
def world(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "secret").write_text("SECRET")
    return ws, victim


def test_a_normal_fixed_path_reads_and_writes(world):
    ws, _ = world
    p = wp.write_text_inside(ws, "kg/INDEX.md", "hello")
    assert p == ws / "kg" / "INDEX.md" and p.read_text() == "hello"
    assert wp.read_text_inside(ws, "kg/INDEX.md") == "hello"
    assert wp.read_bytes_inside(ws, "kg/INDEX.md") == b"hello"


def test_a_linked_directory_component_is_not_followed_for_read(world):
    ws, victim = world
    (ws / "kg").mkdir()
    os.symlink(victim, ws / "kg" / "entities")
    assert wp.read_text_inside(ws, "kg/entities/secret") is None
    assert wp.read_bytes_inside(ws, "kg/entities/secret") is None


def test_a_linked_directory_component_refuses_a_write(world):
    ws, victim = world
    (ws / "kg").mkdir()
    os.symlink(victim, ws / "kg" / "entities")
    with pytest.raises(wp.PathRefused):
        wp.write_text_inside(ws, "kg/entities/x.md", "x")
    assert (victim / "secret").read_text() == "SECRET" and not (victim / "x.md").exists()


def test_a_linked_leaf_is_not_followed_for_read_and_is_replaced_on_write(world):
    ws, victim = world
    (ws / "p").mkdir()
    os.symlink(victim / "secret", ws / "p" / "f")
    assert wp.read_text_inside(ws, "p/f") is None
    wp.write_text_inside(ws, "p/f", "replaced")
    f = ws / "p" / "f"
    assert not f.is_symlink() and f.read_text() == "replaced"
    assert (victim / "secret").read_text() == "SECRET"


def test_a_top_level_linked_component_is_not_followed(world):
    ws, victim = world
    os.symlink(victim, ws / "kg")                      # the whole kg/ dir is a link
    assert wp.read_text_inside(ws, "kg/secret") is None
    with pytest.raises(wp.PathRefused):
        wp.write_text_inside(ws, "kg/x.md", "x")
    assert not (victim / "x.md").exists()


def test_a_second_hard_link_is_no_file(world):
    ws, victim = world
    (ws / "kg").mkdir()
    os.link(victim / "secret", ws / "kg" / "INDEX.md")  # a second name for the victim's inode
    assert wp.read_text_inside(ws, "kg/INDEX.md") is None


def test_unlink_removes_a_link_and_never_follows_it(world):
    ws, victim = world
    (ws / "p").mkdir()
    os.symlink(victim / "secret", ws / "p" / "f")
    wp.unlink_inside(ws, "p/f")
    assert not (ws / "p" / "f").exists() and (victim / "secret").exists()
    os.symlink(victim, ws / "p" / "sub")
    wp.unlink_inside(ws, "p/sub/secret")               # a linked parent: never deletes through it
    assert (victim / "secret").exists()


def test_max_bytes_and_reserved_and_absolute(world):
    ws, _ = world
    wp.write_text_inside(ws, "big.md", "x" * 100)
    assert wp.read_text_inside(ws, "big.md", max_bytes=10) is None
    assert wp.read_text_inside(ws, ".git/config") is None       # reserved
    with pytest.raises(wp.PathRefused):
        wp.write_text_inside(ws, "/etc/passwd", "x")            # absolute
    with pytest.raises(wp.PathRefused):
        wp.write_text_inside(ws, "../escape", "x")              # traversal
    # a reserved dir opens only when allowed
    wp.write_text_inside(ws, ".vexa/workspace.json", "{}", allow=(".vexa",))
    assert wp.read_text_inside(ws, ".vexa/workspace.json", allow=(".vexa",)) == "{}"


def test_an_append_never_waits_on_a_planted_fifo(tmp_path):
    """R6-21: a FIFO at the name made the append's open wait for a reader, holding the worker."""
    import threading
    from workspaces.shared import workspace_paths as wp
    os.mkfifo(tmp_path / "t.jsonl")
    result = {}

    def append():
        try:
            wp.append_text_inside(tmp_path, "t.jsonl", "x\n")
            result["ok"] = True
        except (OSError, ValueError) as exc:
            result["refused"] = exc

    t = threading.Thread(target=append, daemon=True)
    t.start()
    t.join(3)
    if t.is_alive():                     # release the opener the old code left waiting
        fd = os.open(tmp_path / "t.jsonl", os.O_RDONLY | os.O_NONBLOCK)
        t.join(3)
        os.close(fd)
        raise AssertionError("the append waited on a FIFO")
    assert "refused" in result
