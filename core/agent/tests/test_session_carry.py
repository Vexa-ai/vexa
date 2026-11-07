"""ADR-0048 step B, the worker half — a session's transcript is handed to the worker, lives in a
private directory outside every mount for the turn, and is handed back as the next version."""
from __future__ import annotations

import base64
import os

from worker import session_carry
from worker.session_carry import CANONICAL, Carry


def _b(files: dict) -> dict:
    return {k: base64.b64encode(v).decode() for k, v in files.items()}


def test_receive_materialises_under_the_local_session_name(tmp_path):
    c = Carry(tmp_path / "carry", session="pchat-9")
    c.receive({"id": "s-1", "version": 3, "files": _b({
        f".claude/sessions/{CANONICAL}.session": b"sid-1\n",
        f".claude/sessions/{CANONICAL}.turns.jsonl": b'{"key":"k"}\n',
        ".claude/projects/-x/sid-1.jsonl": b'{"type":"user"}\n'})})
    root = tmp_path / "carry"
    assert c.active and c.version == 3 and c.session_id == "s-1"
    assert (root / ".claude/sessions/pchat-9.session").read_bytes() == b"sid-1\n"
    assert (root / ".claude/sessions/pchat-9.turns.jsonl").exists()
    assert (root / ".claude/projects/-x/sid-1.jsonl").exists()


def test_collect_maps_back_to_the_canonical_names(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 0, "files": {}})
    (tmp_path / "carry/.claude/sessions").mkdir(parents=True)
    (tmp_path / "carry/.claude/sessions/main.session").write_bytes(b"sid\n")
    (tmp_path / "carry/.claude/sessions/other.session").write_bytes(b"x\n")   # not this session's
    (tmp_path / "carry/.claude/codex/sessions/2026").mkdir(parents=True)
    (tmp_path / "carry/.claude/codex/sessions/2026/r-sid.jsonl").write_bytes(b"{}\n")
    files = c.collect()
    assert files == {f".claude/sessions/{CANONICAL}.session": b"sid\n",
                     ".claude/codex/sessions/2026/r-sid.jsonl": b"{}\n"}


def test_bundle_event_names_its_base_and_advances(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 2, "files": _b({f".claude/sessions/{CANONICAL}.session": b"a\n"})})
    assert c.bundle_event() is None                    # nothing changed since it arrived
    (tmp_path / "carry/.claude/sessions/main.session").write_bytes(b"b\n")
    ev = c.bundle_event()
    assert ev["type"] == "session-bundle" and ev["session_id"] == "s-1" and ev["base"] == 2
    assert base64.b64decode(ev["files"][f".claude/sessions/{CANONICAL}.session"]) == b"b\n"
    assert c.version == 3                               # the next save is based on what it sent
    assert c.bundle_event() is None


def test_an_older_delivery_never_overwrites_a_newer_local_copy(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 5, "files": _b({f".claude/sessions/{CANONICAL}.session": b"new\n"})})
    c.receive({"id": "s-1", "version": 4, "files": _b({f".claude/sessions/{CANONICAL}.session": b"old\n"})})
    assert (tmp_path / "carry/.claude/sessions/main.session").read_bytes() == b"new\n"
    assert c.version == 5


def test_a_newer_delivery_replaces_the_local_copy_whole(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 1, "files": _b({".claude/projects/a/x.jsonl": b"1"})})
    c.receive({"id": "s-1", "version": 2, "files": _b({".claude/projects/a/y.jsonl": b"2"})})
    assert not (tmp_path / "carry/.claude/projects/a/x.jsonl").exists()
    assert (tmp_path / "carry/.claude/projects/a/y.jsonl").read_bytes() == b"2"


def test_a_bad_path_in_a_bundle_is_refused(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 1, "files": _b({"../escape": b"x", ".claude/ok": b"y"})})
    assert not (tmp_path / "escape").exists()
    assert (tmp_path / "carry/.claude/ok").read_bytes() == b"y"


def test_old_layout_is_adopted_when_the_store_has_nothing_yet(tmp_path):
    legacy = tmp_path / "system"
    (legacy / ".claude/sessions").mkdir(parents=True)
    (legacy / ".claude/sessions/main.session").write_text("sid-7\n")
    (legacy / ".claude/sessions/main.turns.jsonl").write_text('{"key":"k"}\n')
    (legacy / ".claude/projects/-w").mkdir(parents=True)
    (legacy / ".claude/projects/-w/sid-7.jsonl").write_text('{"type":"user"}\n')
    (legacy / ".claude/projects/-w/other.jsonl").write_text("{}\n")
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 0, "files": {}}, legacy_root=legacy)
    root = tmp_path / "carry"
    assert (root / ".claude/sessions/main.session").read_text() == "sid-7\n"
    assert (root / ".claude/projects/-w/sid-7.jsonl").exists()
    assert not (root / ".claude/projects/-w/other.jsonl").exists()   # another thread's transcript
    ev = c.bundle_event()
    assert ev is not None and ev["base"] == 0                        # first save = the adoption


def test_inactive_until_a_bundle_arrives(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    assert not c.active and c.bundle_event() is None


def test_default_root_is_outside_every_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert session_carry.default_root() == tmp_path / "home" / ".vexa-session"
    assert not str(session_carry.default_root()).startswith("/workspace")
    assert os.path.isabs(session_carry.default_root())


# ── the serve loop: the bundle rides the turn in, and goes back before `turn-complete` ─────────
def test_serve_hands_the_conversation_back_before_turn_complete(tmp_path, monkeypatch):
    import json

    from tests.conftest import signed_turn
    from tests.test_worker import FakeStream
    from worker import engine
    from worker.worker import serve

    carry = Carry(tmp_path / "carry", session="main")
    monkeypatch.setattr(engine, "_CARRY", carry)
    monkeypatch.setattr(engine, "refresh_desk_readme", lambda *a, **k: None)
    seen_roots = []

    def turn(prompt):
        root = engine._continuity_root(tmp_path / "ws")
        seen_roots.append(root)
        (root / ".claude/sessions").mkdir(parents=True, exist_ok=True)
        (root / ".claude/sessions/main.session").write_text(f"sid-{prompt}\n")
        yield {"type": "message-delta", "text": "ok"}

    bundle = {"id": "s-9", "version": 4, "files": _b({f".claude/sessions/{CANONICAL}.session": b"sid-old\n"})}
    s = FakeStream(inbox=[("1-0", signed_turn({"prompt": "hi", "session_bundle": bundle}))])
    serve(s, out_topic="o", in_topic="i", turn=turn, start={}, idle_ms=10)
    evs = s.events()
    kinds = [e["type"] for e in evs]
    assert seen_roots == [tmp_path / "carry"]                # the turn ran on the carried copy
    assert kinds.index("session-bundle") < kinds.index("turn-complete")
    handed = next(e for e in evs if e["type"] == "session-bundle")
    assert handed["session_id"] == "s-9" and handed["base"] == 4
    assert base64.b64decode(handed["files"][f".claude/sessions/{CANONICAL}.session"]) == b"sid-hi\n"


def test_serve_without_a_bundle_keeps_the_workspace_layout(tmp_path, monkeypatch):
    from tests.test_worker import FakeStream, _msg
    from worker import engine
    from worker.worker import serve

    carry = Carry(tmp_path / "carry", session="main")
    monkeypatch.setattr(engine, "_CARRY", carry)
    monkeypatch.setattr(engine, "refresh_desk_readme", lambda *a, **k: None)
    roots = []

    def turn(prompt):
        roots.append(engine._continuity_root(tmp_path / "ws"))
        yield {"type": "message-delta", "text": "ok"}

    s = FakeStream(inbox=[_msg("1-0", "hi")])
    serve(s, out_topic="o", in_topic="i", turn=turn, start={}, idle_ms=10)
    assert roots == [tmp_path / "ws"]
    assert "session-bundle" not in [e["type"] for e in s.events()]


# ── a shared turn with no common workspace works in a per-session scratch folder ──────────────
def test_shared_scratch_is_per_session_and_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    a = session_carry.shared_scratch("s-aaa")
    b = session_carry.shared_scratch("s-bbb")
    assert a != b and a.is_dir() and b.is_dir()
    assert str(a).startswith(str(tmp_path / "home"))
    (a / "note.md").write_text("x")
    session_carry.discard(a)
    assert not a.exists() and b.is_dir()


def test_shared_scratch_refuses_a_name_that_is_not_one_component(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    import pytest
    with pytest.raises(ValueError):
        session_carry.shared_scratch("../escape")


def test_the_worker_uses_scratch_only_when_told(tmp_path, monkeypatch):
    from worker import engine
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    assert engine.turn_workdir(ws, "s-1", {}) == ws
    got = engine.turn_workdir(ws, "s-1", {"VEXA_SHARED_SCRATCH": "1"})
    assert got == session_carry.shared_scratch("s-1") and got != ws


# ── a second participant resumes a transcript written under another cwd-slug (review S9) ─────────
# The claude-code CLI keys a transcript by the cwd it was written from
# (``~/.claude/projects/<cwd with every non-alphanumeric as '-'>/<sid>.jsonl``), and two participants
# of one shared session can run with different cwds (another target, or their own scratch folder:
# ``.vexa-scratch/main`` vs ``.vexa-scratch/<store id>``). Checked live against the worker image's
# CLI (2.1.293): ``--resume <sid>`` from cwd Y finds the transcript under cwd X's slug — it looks the
# sid up in every folder of ``~/.claude/projects`` — and appends to that same file; an unknown sid
# still stops with "No conversation found". That holds only while the WHOLE projects tree is on the
# resuming worker's disk under the one ``~/.claude/projects`` link, so these tests pin exactly that.

def test_a_transcript_under_another_cwd_slug_reaches_the_resuming_participant(tmp_path, monkeypatch):
    from llm import claude_code

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    slug_a = "-tmp--vexa-scratch-main"                      # participant A's cwd
    a_rel = f".claude/projects/{slug_a}/sid-1.jsonl"
    # participant B's worker: its own local name for the thread, a different cwd
    c = Carry(tmp_path / "carry", session="pchat-9")
    c.receive({"id": "s-1", "version": 4, "files": _b({
        f".claude/sessions/{CANONICAL}.session": b"sid-1\n",
        a_rel: b'{"type":"user","sessionId":"sid-1"}\n'})})
    claude_code._link_chat_into_workspace(c.root)
    # the slug is kept as written — not re-homed to B's cwd — and visible through B's HOME
    assert (home / a_rel).read_bytes() == b'{"type":"user","sessionId":"sid-1"}\n'
    assert claude_code.ClaudeCodeHarness().transcript_bytes(c.root, "sid-1") > 0
    # B's turn appends to A's file (what the CLI does); the hand-back keeps it under A's slug
    with open(home / a_rel, "ab") as f:
        f.write(b'{"type":"user","sessionId":"sid-1","text":"B"}\n')
    ev = c.bundle_event()
    assert set(ev["files"]) == {f".claude/sessions/{CANONICAL}.session", a_rel}
    assert base64.b64decode(ev["files"][a_rel]).count(b"\n") == 2


def test_collect_hands_back_transcripts_under_every_cwd_slug(tmp_path):
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 0, "files": {}})
    for slug in ("-tmp--vexa-scratch-main", "-workspaces-team-a"):
        d = tmp_path / "carry/.claude/projects" / slug
        d.mkdir(parents=True)
        (d / "sid-1.jsonl").write_bytes(b"{}\n")
    assert set(c.collect()) == {".claude/projects/-tmp--vexa-scratch-main/sid-1.jsonl",
                                ".claude/projects/-workspaces-team-a/sid-1.jsonl"}


def test_openai_agent_resumes_a_transcript_under_another_cwd_slug(tmp_path):
    from llm.openai_agent import _Transcript

    chat, x, y = tmp_path / "carry", tmp_path / "x", tmp_path / "y"
    for d in (chat, x, y):
        d.mkdir()
    _Transcript(chat, x, "sid").append({"type": "user", "n": 1})
    t = _Transcript(chat, y, "sid")                          # the other participant's cwd
    assert t.exists()
    t.append({"type": "user", "n": 2})
    files = [p for p in (chat / ".claude/projects").rglob("*.jsonl")]
    assert len(files) == 1 and files[0].read_text().count("\n") == 2


def test_a_delivery_marked_replace_winds_back_a_copy_the_store_refused(tmp_path):
    """The worker counts its own hand-back as the next version before the store has answered. When
    the store refused it, agent-api marks the next delivery ``replace``: the head it carries wins over
    the local copy, and the next hand-back is based on that head."""
    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 1, "files": _b({f".claude/sessions/{CANONICAL}.session": b"head\n"})})
    (tmp_path / "carry/.claude/sessions/main.session").write_bytes(b"refused\n")
    assert c.bundle_event()["base"] == 1 and c.version == 2
    c.receive({"id": "s-1", "version": 1, "replace": True,
               "files": _b({f".claude/sessions/{CANONICAL}.session": b"head\n"})})
    assert (tmp_path / "carry/.claude/sessions/main.session").read_bytes() == b"head\n"
    assert c.version == 1
    (tmp_path / "carry/.claude/sessions/main.session").write_bytes(b"next\n")
    assert c.bundle_event()["base"] == 1
