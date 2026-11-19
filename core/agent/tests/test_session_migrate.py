"""ADR-0048 decision 10 — existing transcripts move into the store: idempotent, verified by read-back,
counts-only logs, nothing deleted unless asked and verified."""
from __future__ import annotations

import pytest

from control_plane import session_migrate, session_sync
from control_plane.session_store import SessionConflict, SessionStore
from tests._session_fakes import MemBlobs


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def _thread(base, name, sid, text="hello"):
    (base / ".claude/sessions").mkdir(parents=True, exist_ok=True)
    (base / f".claude/sessions/{name}.session").write_text(sid + "\n")
    (base / f".claude/sessions/{name}.turns.jsonl").write_text('{"key":"k","user_text":"hi"}\n')
    (base / ".claude/projects/-w").mkdir(parents=True, exist_ok=True)
    (base / f".claude/projects/-w/{sid}.jsonl").write_text(
        '{"type":"user","message":{"content":"%s"}}\n' % text)


@pytest.fixture()
def volume(tmp_path):
    root = tmp_path / "ws"
    _thread(root / ".system" / "alice", "main", "sid-a1")
    _thread(root / ".system" / "alice", "pchat-2", "sid-a2")
    _thread(root / "bob", "main", "sid-b1")                     # legacy: in the desk itself
    (root / "bob" / ".claude/projects/-w/unrelated.jsonl").write_text("{}\n")
    (root / "_global").mkdir(parents=True)
    return root


def test_copies_every_thread_verified(store, volume, caplog):
    counts = session_migrate.migrate(store, volume)
    assert counts["people"] == 2 and counts["threads"] == 3
    assert counts["copied"] == 3 and counts["verified"] == 3 and counts["mismatched"] == 0
    row = store.by_name("alice", "pchat-2")
    files = store.load_bundle(row["id"])["files"]
    assert files[".claude/sessions/conversation.session"] == b"sid-a2\n"
    assert ".claude/projects/-w/sid-a2.jsonl" in files
    bob = store.load_bundle(store.by_name("bob", "main")["id"])["files"]
    assert ".claude/projects/-w/unrelated.jsonl" not in bob
    assert session_sync.history(store, row)[0]["role"] == "user"
    assert "sid-a" not in caplog.text and "alice" not in caplog.text      # counts only


def test_is_idempotent(store, volume):
    session_migrate.migrate(store, volume)
    again = session_migrate.migrate(store, volume)
    assert again["copied"] == 0 and again["skipped"] == 3


def test_dry_run_writes_nothing(store, volume):
    counts = session_migrate.migrate(store, volume, dry_run=True)
    assert counts["copied"] == 3 and store.by_name("alice", "main") is None


def test_nothing_is_deleted_unless_asked(store, volume):
    session_migrate.migrate(store, volume)
    assert (volume / ".system/alice/.claude/sessions/main.session").exists()


def test_clean_removes_only_verified_copies(store, volume):
    counts = session_migrate.migrate(store, volume, clean=True)
    assert counts["cleaned_files"] == 9
    assert not (volume / ".system/alice/.claude/sessions/main.session").exists()
    assert (volume / "bob/.claude/projects/-w/unrelated.jsonl").exists()


# ── S8: people come from the system tier and the desks, never from a shared workspace ─────────
def _group(root, slug, kind_file=True):
    ws = root / slug
    _thread(ws, "main", "sid-g1")
    (ws / "policy").mkdir(parents=True, exist_ok=True)
    (ws / "policy/members.json").write_text('[{"subject": "alice", "role": "owner"}]')
    return ws


def test_a_shared_workspace_is_not_a_person(store, volume):
    ws = _group(volume, "team-abc123")
    counts = session_migrate.migrate(store, volume, clean=True)
    assert counts["people"] == 2 and counts["threads"] == 3
    assert store.by_name("team-abc123", "main") is None
    assert (ws / ".claude/sessions/main.session").exists()          # --clean never touched it


def test_a_group_workspace_by_kind_is_not_a_person(store, volume):
    ws = volume / "crew"
    _thread(ws, "main", "sid-c1")
    from workspaces.shared.workspace_id import mint_id, write_workspace_json
    write_workspace_json(ws, id=mint_id(), kind="group", created="2026-10-13")
    session_migrate.migrate(store, volume)
    assert store.by_name("crew", "main") is None


def test_a_mismatched_read_back_is_retried(store, volume, monkeypatch):
    real = store.load_bundle
    bad = {"n": 1}

    def _flaky(sid, version=None):
        got = real(sid, version)
        if bad["n"] > 0:
            bad["n"] -= 1
            return {"version": got["version"], "files": {"x": b"corrupt"}}
        return got

    monkeypatch.setattr(store, "load_bundle", _flaky)
    counts = session_migrate.migrate(store, volume)
    assert counts["verified"] == 3 and counts["mismatched"] == 0 and counts["retried"] == 1


def test_a_persistent_mismatch_leaves_no_head_and_the_next_run_copies_again(store, volume, monkeypatch):
    real = store.load_bundle
    monkeypatch.setattr(store, "load_bundle",
                        lambda sid, version=None: {"version": 1, "files": {"x": b"corrupt"}})
    counts = session_migrate.migrate(store, volume, clean=True)
    assert counts["mismatched"] == 3 and counts["verified"] == 0 and counts["cleaned_files"] == 0
    assert store.by_name("alice", "main") is None                     # no bad version left as head
    monkeypatch.setattr(store, "load_bundle", real)
    again = session_migrate.migrate(store, volume)
    assert again["verified"] == 3


def test_a_conflict_on_one_thread_is_counted_and_the_run_goes_on(store, volume, monkeypatch):
    real = store.save_bundle

    def _save(sid, base, files):
        if store.get(sid)["name"] == "pchat-2":
            raise SessionConflict(sid, base, 1)
        return real(sid, base, files)

    monkeypatch.setattr(store, "save_bundle", _save)
    counts = session_migrate.migrate(store, volume, clean=True)
    assert counts["conflicts"] == 1 and counts["verified"] == 2
    assert (volume / ".system/alice/.claude/sessions/pchat-2.session").exists()


def test_clean_on_a_later_run_removes_only_threads_whose_head_is_the_copy(store, volume):
    session_migrate.migrate(store, volume)
    sid = store.by_name("bob", "main")["id"]
    store.save_bundle(sid, 1, {"a": b"a live turn since"})
    counts = session_migrate.migrate(store, volume, clean=True)
    assert counts["skipped"] == 3
    assert not (volume / ".system/alice/.claude/sessions/main.session").exists()
    assert (volume / "bob/.claude/sessions/main.session").exists()     # head moved on: kept


def test_retire_removes_old_layout_files_the_store_already_carries_and_keeps_diverged_ones(store, tmp_path):
    """With the store on, files left in the old layout for a conversation the store holds leave the
    workspace when the store's head carries them (each file the same or extended); a diverged leftover
    is counted and kept for the operator, never lost — moved out of every mount, not deleted."""
    root = tmp_path / "ws"
    base = root / ".system" / "alice"
    _thread(base, "main", "sid-1")
    files, _ = session_migrate.collect_thread(session_migrate.continuity_roots(root, "alice"), "main")
    row = store.create("alice", name="main")
    store.save_bundle(row["id"], 0, {k: v + b"later turn\n" for k, v in files.items()})
    _thread(base, "other", "sid-2")
    row2 = store.create("alice", name="other")
    store.save_bundle(row2["id"], 0, {"x": b"something else"})
    counts = session_migrate.migrate(store, root, clean=True, retire=True)
    assert counts["retired"] == 1 and counts["diverged"] == 1
    assert not (base / ".claude/sessions/main.session").exists()
    assert not (base / ".claude/sessions/other.session").exists()
    kept = list((root / session_migrate.ARCHIVE).glob("*/.system/alice/.claude/sessions/other.session"))
    assert len(kept) == 1 and kept[0].read_text() == "sid-2\n"


# ── session-store (0.13.3): every chat leaves the workspaces; reversible; count-for-count ─────
def test_retire_leaves_no_conversation_under_any_mount_and_counts_match(store, volume):
    before = session_migrate.old_layout_files(volume)
    counts = session_migrate.migrate(store, volume, clean=True, retire=True)
    assert counts["threads"] == 3 and counts["verified"] == 3
    assert session_migrate.old_layout_files(volume) == 0
    assert counts["archived_files"] == before == counts["cleaned_files"]
    # what is left in bob's desk is not a conversation and was never touched
    assert (volume / "bob/.claude/projects/-w/unrelated.jsonl").exists()
    assert all(store.by_name(s, n) for s, n in (("alice", "main"), ("alice", "pchat-2"), ("bob", "main")))


def test_retire_is_idempotent(store, volume):
    session_migrate.migrate(store, volume, clean=True, retire=True)
    again = session_migrate.migrate(store, volume, clean=True, retire=True)
    assert again["threads"] == 0 and again["copied"] == 0 and again["archived_files"] == 0


def test_restore_puts_every_file_back_byte_for_byte_and_never_overwrites(store, volume):
    snap = {p.relative_to(volume): p.read_bytes() for p in volume.rglob("*") if p.is_file()}
    session_migrate.migrate(store, volume, clean=True, retire=True)
    (volume / ".system/alice/.claude/sessions").mkdir(parents=True, exist_ok=True)
    (volume / ".system/alice/.claude/sessions/main.session").write_text("newer\n")   # written since
    counts = session_migrate.restore(volume)
    assert counts["restored"] == len(snap) - 2 and counts["kept_newer"] == 1
    assert (volume / ".system/alice/.claude/sessions/main.session").read_text() == "newer\n"
    for rel, data in snap.items():
        if str(rel) != ".system/alice/.claude/sessions/main.session" and not str(rel).startswith("_global"):
            if (volume / rel).exists():
                assert (volume / rel).read_bytes() == data
    assert session_migrate.restore(volume)["restored"] == 0


def test_the_archive_is_no_person_and_no_workspace(store, volume):
    session_migrate.migrate(store, volume, clean=True, retire=True)
    assert session_migrate.ARCHIVE.startswith(".")
    assert session_migrate.ARCHIVE not in session_migrate.people(volume)


def test_retire_rewrites_the_stock_system_readme_and_leaves_an_edited_one(store, volume):
    from control_plane import system_mounts
    (volume / ".system/alice/README.md").write_text(system_mounts.OLD_SYSTEM_README)
    (volume / ".system/carol").mkdir(parents=True)
    (volume / ".system/carol/README.md").write_text("my own words\n")
    counts = session_migrate.migrate(store, volume, clean=True, retire=True)
    assert counts["readmes_refreshed"] == 1
    assert (volume / ".system/alice/README.md").read_text() == system_mounts.SYSTEM_README
    assert (volume / ".system/carol/README.md").read_text() == "my own words\n"
    assert "session store" in system_mounts.SYSTEM_README
