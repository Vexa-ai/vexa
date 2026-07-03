"""Multi-session chat — the unit tests for REAL conversation threads in the ONE user workspace.

Covers the three pieces that move together:
  1. dispatch_id keys chat on (subject, session); default "main"; meeting/digest UNCHANGED.
  2. per-session continuity file (namespaced; "main" migrates from the legacy single-thread file).
  3. the durable session index (upsert / list-ordering / drop), over a redis fake.
"""
from __future__ import annotations

from pathlib import Path

from shared import units
from control_plane.api import _Sessions, _truncate_title
from control_plane.dispatch import _without_chat_session
from worker.worker import _session_file


# ── 1. dispatch_id ────────────────────────────────────────────────────────────

def _chat_inv(subject: str, session: str | None):
    ctx: dict = {"kind": "none"}
    if session is not None:
        ctx["session"] = session
    return units.make_dispatch(
        subject=subject, trigger="message",
        start=units.entrypoint(inline="hi"), context=ctx,
    )


def test_dispatch_id_keys_chat_on_session():
    a = units.dispatch_id(_chat_inv("u1", "work"))
    b = units.dispatch_id(_chat_inv("u1", "personal"))
    assert a == "agent-u1-chat-work"
    assert b == "agent-u1-chat-personal"
    assert a != b  # distinct threads → distinct warm units


def test_dispatch_id_defaults_to_main():
    assert units.dispatch_id(_chat_inv("u1", None)) == "agent-u1-chat-main"
    # an explicit "main" and an absent session collapse to the same unit
    assert units.dispatch_id(_chat_inv("u1", "main")) == "agent-u1-chat-main"


def test_dispatch_id_meeting_and_digest_unchanged():
    meeting = units.make_dispatch(
        subject="u_live", trigger="transcription",
        start=units.entrypoint(inline="brief"),
        context={"kind": "meeting", "meeting": {"meeting_id": "abc", "session_uid": "abc"}},
    )
    assert units.dispatch_id(meeting) == "agent-meet-abc"  # session never touches the meeting key

    scheduled = units.make_dispatch(
        subject="u1", trigger="scheduled", start=units.entrypoint(inline="digest"),
    )
    did = units.dispatch_id(scheduled)
    assert did.startswith("agent-u1-scheduled-") and "chat" not in did


def test_chat_session_helper_and_strip():
    inv = _chat_inv("u1", "work")
    assert units.chat_session(inv) == "work"
    # the contract-validation copy drops the agent-api-internal session hint
    cleaned = _without_chat_session(inv)
    assert "session" not in cleaned["context"] and cleaned["context"]["kind"] == "none"
    assert inv["context"]["session"] == "work"  # original untouched


# ── 2. per-session continuity ─────────────────────────────────────────────────

def test_session_file_is_namespaced(tmp_path: Path):
    f = _session_file(tmp_path, "work")
    assert f == tmp_path / ".claude" / "sessions" / "work.session"
    distinct = _session_file(tmp_path, "personal")
    assert distinct != f  # threads don't collide


def test_main_migrates_from_legacy_single_thread(tmp_path: Path):
    legacy = tmp_path / ".claude" / ".session"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("LEGACY_SID")

    f = _session_file(tmp_path, "main")
    assert f.exists() and f.read_text() == "LEGACY_SID"  # adopted, not lost
    assert f == tmp_path / ".claude" / "sessions" / "main.session"


def test_main_does_not_migrate_when_namespaced_exists(tmp_path: Path):
    ns = tmp_path / ".claude" / "sessions" / "main.session"
    ns.parent.mkdir(parents=True)
    ns.write_text("NEW_SID")
    (tmp_path / ".claude" / ".session").write_text("OLD_SID")

    assert _session_file(tmp_path, "main").read_text() == "NEW_SID"  # namespaced wins


def test_non_main_never_migrates_legacy(tmp_path: Path):
    (tmp_path / ".claude").mkdir(parents=True)
    (tmp_path / ".claude" / ".session").write_text("LEGACY")
    f = _session_file(tmp_path, "work")
    assert not f.exists()  # only "main" adopts the legacy file


# ── 3. durable session index ──────────────────────────────────────────────────

class _FakeRedis:
    """A tiny redis fake covering the hash/set ops the index uses."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict] = {}
        self.sets: dict[str, set] = {}

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in (mapping or {}).items()})

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.hashes.pop(key, None)


def _index_cases():
    return [_Sessions(), _Sessions(_FakeRedis())]


def test_index_upsert_titles_and_lists():
    for sess in _index_cases():
        sess.upsert("u1", "work", title="Plan the launch")
        rows = sess.list("u1")
        assert len(rows) == 1
        assert rows[0]["session"] == "work" and rows[0]["title"] == "Plan the launch"


def test_index_orders_most_recent_first():
    for sess in _index_cases():
        sess.upsert("u1", "old", title="old")
        sess.upsert("u1", "new", title="new")
        sess.upsert("u1", "old")  # re-touch old → it becomes most-recent
        order = [r["session"] for r in sess.list("u1")]
        assert order == ["old", "new"]


def test_index_upsert_preserves_title_on_retouch():
    for sess in _index_cases():
        sess.upsert("u1", "work", title="First prompt")
        sess.upsert("u1", "work")  # no title → keep the original
        assert sess.list("u1")[0]["title"] == "First prompt"


def test_index_drop_removes_thread():
    for sess in _index_cases():
        sess.upsert("u1", "work", title="w")
        sess.upsert("u1", "keep", title="k")
        sess.drop("u1", "work")
        assert [r["session"] for r in sess.list("u1")] == ["keep"]


def test_truncate_title():
    assert _truncate_title("  hello   world ") == "hello world"
    long = "x" * 100
    out = _truncate_title(long)
    assert len(out) == 60 and out.endswith("…")
