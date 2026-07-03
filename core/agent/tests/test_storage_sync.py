"""O-AG-2 — the ``WorkspaceStoragePort`` (S3/MinIO sync) against a FAKE object-store transport.

Mirrors the parent ``workspace.py`` ``aws s3 sync`` up/down: ``sync_down`` GETs objects into the
local dir, ``sync_up`` PUTs the local tree (deleting extraneous keys), and BOTH honor the excludes
(the ``.claude/.session`` ephemera the parent never syncs). No boto3, no network.
"""
from __future__ import annotations

from pathlib import Path

from .fakes import FakeStorage

# The parent's exclude set (workspace.py _SYNC_EXCLUDES), as path prefixes.
EXCLUDES = (".claude/.session", ".claude/.chat-prompt.txt", ".claude/.agent-prompt.txt")


def test_sync_down_fetches_objects_and_honors_excludes(tmp_path: Path):
    store = FakeStorage({
        "kg/entities/meeting/m1.md": "---\ntype: meeting\n---\nbody",
        "README.md": "# memory",
        ".claude/.session": "EPHEMERAL-should-not-land",
    })
    local = tmp_path / "work"
    local.mkdir()

    fetched = store.sync_down(str(local), excludes=EXCLUDES)

    # the excluded ephemeral object was NOT fetched
    assert sorted(fetched) == ["README.md", "kg/entities/meeting/m1.md"]
    assert (local / "kg/entities/meeting/m1.md").read_text().startswith("---")
    assert not (local / ".claude/.session").exists()
    # the op log records the right GETs (and only those)
    assert store.ops == ["GET README.md", "GET kg/entities/meeting/m1.md"]


def test_sync_up_puts_local_tree_and_deletes_extraneous(tmp_path: Path):
    # store already has an old object that the local tree no longer carries → must be DELETEd.
    store = FakeStorage({"kg/entities/contact/old.md": "stale", ".claude/.session": "keep-me"})
    local = tmp_path / "work"
    (local / "kg/entities/meeting").mkdir(parents=True)
    (local / "kg/entities/meeting/m1.md").write_text("fresh")
    (local / ".claude").mkdir()
    (local / ".claude/.session").write_text("LOCAL-EPHEMERAL")  # excluded → never PUT

    put = store.sync_up(str(local), excludes=EXCLUDES)

    assert put == ["kg/entities/meeting/m1.md"]                  # only the non-excluded local file
    assert store.objects["kg/entities/meeting/m1.md"] == "fresh"
    assert "kg/entities/contact/old.md" not in store.objects     # --delete dropped the stale key
    # the excluded ephemeral was neither PUT nor DELETEd — left exactly as it was
    assert store.objects[".claude/.session"] == "keep-me"
    assert "PUT .claude/.session" not in store.ops
    assert "DELETE .claude/.session" not in store.ops
    assert "DELETE kg/entities/contact/old.md" in store.ops


def test_roundtrip_down_then_up_is_stable(tmp_path: Path):
    store = FakeStorage({"a.md": "A", "b.md": "B"})
    local = tmp_path / "work"
    local.mkdir()
    store.sync_down(str(local))
    put = store.sync_up(str(local))
    assert sorted(put) == ["a.md", "b.md"]
    assert store.objects == {"a.md": "A", "b.md": "B"}
