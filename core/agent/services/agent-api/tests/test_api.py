"""Front-door L2 tests — the agent-api HTTP surface over fakes (no runtime, no claude needed).

Proves: /health is live; /invocations validates + dispatches (and 400s a bad envelope); /api/chat
spawns a now-dispatch and streams its Stream back as SSE; chat is an honest 501 with no relay wired.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api.api import create_app
from agent_api.config import load_settings
from agent_api.dispatch import Dispatcher

VALID_INV = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}],
    "trigger": "message",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


class _FakeRuntime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    """A StreamReader fake — yields the dispatch's UnitEvents (what redis XREAD would relay)."""
    def read(self, unit_id):
        yield {"type": "message-delta", "text": "hi"}
        yield {"type": "commit", "sha": "abc123"}


def _client(stream_reader=None) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), stream_reader=stream_reader,
    ))


def test_health_ok():
    r = _client().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_models_reports_chat_and_workspace_streaming_model(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader

    meeting_cfg = tmp_path / "u_jane" / "agents" / "meeting.md"
    meeting_cfg.parent.mkdir(parents=True)
    meeting_cfg.write_text("---\nmodel: openrouter/free\n---\n")
    c = TestClient(create_app(
        Dispatcher(
            load_settings(agent_model="deepseek/deepseek-v4-flash", meeting_model="deepseek/deepseek-v4-flash"),
            _FakeRuntime(),
            _FakeIdentity(),
        ),
        reader=WorkspaceReader(str(tmp_path)),
    ))

    r = c.get("/api/models", params={"subject": "u_jane"})

    assert r.status_code == 200
    assert r.json()["chat_model"] == "deepseek/deepseek-v4-flash"
    assert r.json()["streaming_model"] == "openrouter/free"


def test_invocations_dispatches():
    r = _client().post("/invocations", json=VALID_INV)
    assert r.status_code == 202 and r.json()["workload_id"]


def test_invocations_rejects_nonconformant():
    r = _client().post("/invocations", json={"trigger": "message"})  # missing identity/workspaces/start
    assert r.status_code == 400


def test_chat_501_without_reader():
    r = _client().post("/api/chat", json={"prompt": "hi", "subject": "u"})
    assert r.status_code == 501


def test_chat_streams_sse_and_records_session():
    c = _client(_FakeReader())
    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    body = r.text
    assert "data: " in body and '"message-delta"' in body and '"commit"' in body
    sessions = c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"]
    assert any(s["session"] == "s1" for s in sessions)
    assert r.headers["X-Unit-Id"] == "agent-u_jane-chat-s1"  # the per-thread warm unit id


def test_chat_subject_is_server_derived_from_header_not_client_body():
    """Stage 1 (P20): the dispatch subject MUST come from the authenticated ``X-User-Id`` header, never the
    client-supplied ``body.subject``. The warm-unit id encodes the subject, so it reveals which one was used."""
    c = _client(_FakeReader())
    r = c.post(
        "/api/chat",
        headers={"X-User-Id": "u_alice"},
        json={"prompt": "hi", "subject": "u_evil", "session": "s1"},  # body subject is a spoof attempt
    )
    assert r.status_code == 200
    assert r.headers["X-Unit-Id"] == "agent-u_alice-chat-s1", \
        f"subject leaked from client body — got {r.headers.get('X-Unit-Id')!r}, expected the header identity"
    # the spoofed body subject owns nothing; the header identity owns the session
    evil = c.get("/api/sessions", headers={"X-User-Id": "u_evil"}).json()["sessions"]
    assert not any(s["session"] == "s1" for s in evil)
    alice = c.get("/api/sessions", headers={"X-User-Id": "u_alice"}).json()["sessions"]
    assert any(s["session"] == "s1" for s in alice)


def test_get_routes_derive_subject_from_header_not_a_required_query(tmp_path):
    """REGRESSION LOCK (P20 · Stage 1↔4): EVERY subject-addressed GET route must derive ``subject`` from
    the authenticated ``X-User-Id`` header — NOT require a ``?subject=`` query. Stage 4 dropped the client
    ``subject`` from the terminal; a build whose GET routes still required the query 422'd
    (``{"loc":["query","subject"],"msg":"Field required"}``) and silently broke sessions/routines/workspace.
    Called with ONLY ``X-User-Id`` (no subject query), none of these may 422."""
    from agent_api.workspace_reader import WorkspaceReader

    (tmp_path / "u_alice").mkdir(parents=True, exist_ok=True)
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)),
    ))
    h = {"X-User-Id": "u_alice"}
    for path in (
        "/api/sessions",
        "/api/routines",
        "/api/models",
        "/api/workspace/tree",
        "/api/workspace/git",
        "/api/workspace/file?path=README.md",
    ):
        r = c.get(path, headers=h)
        assert r.status_code != 422, f"{path} still REQUIRES ?subject= (P20 regression) — 422: {r.text}"


def test_chat_reset_drops_session_and_continuity_file(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader

    # plant a thread's continuity file in the subject's workspace
    sess_dir = tmp_path / "u_jane" / ".claude" / "sessions"
    sess_dir.mkdir(parents=True)
    (sess_dir / "s1.session").write_text("SID")
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        stream_reader=_FakeReader(), reader=WorkspaceReader(str(tmp_path)),
    ))
    c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert any(s["session"] == "s1" for s in c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"])

    r = c.post("/api/chat/reset", json={"prompt": "", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    assert not (sess_dir / "s1.session").exists()  # continuity file deleted
    assert not any(s["session"] == "s1" for s in c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"])


def test_chat_defaults_session_to_main(tmp_path):
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), stream_reader=_FakeReader(),
    ))
    r = c.post("/api/chat", json={"prompt": "no session given", "subject": "u_jane"})
    assert r.headers["X-Chat-Session"] == "main"
    assert r.headers["X-Unit-Id"] == "agent-u_jane-chat-main"
    rows = c.get("/api/sessions", params={"subject": "u_jane"}).json()["sessions"]
    assert rows[0]["session"] == "main" and rows[0]["title"] == "no session given"


def test_meeting_start_threads_transcript_tail_cursor(monkeypatch):
    import redis

    class FakeRedis:
        def xrevrange(self, stream, count=1):
            assert stream == "tc:meeting:abc-defg-hij"
            assert count == 1
            return [("42-0", {})]

    monkeypatch.setattr(redis, "from_url", lambda *_args, **_kwargs: FakeRedis())
    runtime = _FakeRuntime()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), redis_url="redis://test",
    ))

    r = c.post("/api/meeting/start", json={"platform": "google_meet", "native_id": "abc-defg-hij", "subject": "u_jane"})

    assert r.status_code == 202
    env = runtime.spawned[0][2]
    assert env["VEXA_TRANSCRIPT_START_ID"] == "42-0"


def test_meeting_process_resumes_from_cursor_gap_fill(monkeypatch):
    """Phase C (c): on re-enable, /api/meeting/process dispatches a SINGLE catch-up pass starting from the
    persisted cursor (last cleaned raw id) — the gap [cursor → tail] — not from the tail or 0."""
    import redis

    class FakeRedis:
        def __init__(self):
            self.kv = {"proc:meeting:m9:cursor": "37-0"}  # we cleaned up to 37-0 last time

        def set(self, k, v):
            self.kv[k] = v

        def get(self, k):
            return self.kv.get(k)

        def delete(self, k):
            self.kv.pop(k, None)

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: FakeRedis())
    runtime = _FakeRuntime()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), redis_url="redis://test",
    ))

    r = c.post("/api/meeting/process", json={"native_id": "m9", "on": True, "subject": "u_jane"})

    assert r.status_code == 202
    assert r.json()["resumed_from"] == "37-0"
    env = runtime.spawned[0][2]
    assert env["VEXA_TRANSCRIPT_START_ID"] == "37-0"  # ONE catch-up pass from the cursor, not the tail


def test_meeting_process_no_cursor_processes_full_history(monkeypatch):
    """A never-processed meeting has no cursor ⇒ start from 0-0 (process the whole transcript once)."""
    import redis

    class FakeRedis:
        kv: dict = {}

        def set(self, k, v):
            type(self).kv[k] = v

        def get(self, k):
            return None

        def delete(self, k):
            type(self).kv.pop(k, None)

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: FakeRedis())
    runtime = _FakeRuntime()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), redis_url="redis://test",
    ))

    r = c.post("/api/meeting/process", json={"native_id": "m10", "on": True})

    assert r.json()["resumed_from"] == "0-0"
    assert runtime.spawned[0][2]["VEXA_TRANSCRIPT_START_ID"] == "0-0"


def test_meeting_process_off_freezes_cursor(monkeypatch):
    """OFF clears the processing flag but LEAVES the cursor frozen for the next re-enable."""
    import redis

    class FakeRedis:
        def __init__(self):
            self.kv = {"proc:meeting:m9:on": "1", "proc:meeting:m9:cursor": "37-0"}

        def set(self, k, v):
            self.kv[k] = v

        def get(self, k):
            return self.kv.get(k)

        def delete(self, k):
            self.kv.pop(k, None)

    fake = FakeRedis()
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
    ))

    r = c.post("/api/meeting/process", json={"native_id": "m9", "on": False})

    assert r.json()["processing"] is False
    assert "proc:meeting:m9:on" not in fake.kv          # flag cleared
    assert fake.kv["proc:meeting:m9:cursor"] == "37-0"  # cursor frozen


def test_meeting_stream_seeds_recent_tail_without_replaying_from_zero(monkeypatch):
    import json
    import redis

    class FakeRedis:
        def __init__(self):
            self.first_xread = None
            self.calls = 0

        def xrevrange(self, stream, count=1):
            if stream == "tc:meeting:abc":
                return [
                    ("9-0", {"payload": json.dumps({"type": "transcription", "segments": [{"speaker": "Recent", "text": "tail", "start": 9, "segment_id": "recent"}]})}),
                    ("8-0", {"payload": json.dumps({"type": "transcription", "segments": [{"speaker": "Older", "text": "still recent", "start": 8, "segment_id": "older"}]})}),
                ]
            if stream == "unit:agent-meet-abc:out":
                return [
                    ("4-0", {"event": json.dumps({"type": "note", "note": {"id": "n1", "text": "processed tail"}})}),
                ]
            return []

        def xread(self, streams, count=500, block=15000):
            self.calls += 1
            if self.first_xread is None:
                self.first_xread = dict(streams)
                return [("tc:meeting:abc", [("10-0", {"payload": json.dumps({"type": "session_end"})})])]
            return []

    fake = FakeRedis()
    monkeypatch.setattr(redis, "from_url", lambda *_args, **_kwargs: fake)
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
    ))

    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "abc", "session_uid": "abc"}) as r:
        body = "".join(r.iter_text())

    assert r.status_code == 200
    assert '"text": "still recent"' in body
    assert '"text": "tail"' in body
    assert '"processed tail"' in body
    assert '"meeting-end"' in body
    assert fake.first_xread == {"tc:meeting:abc": "9-0", "unit:agent-meet-abc:out": "4-0"}


def test_workspace_read_and_traversal_guard(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader
    p = tmp_path / "u_jane" / "kg" / "entities" / "person"
    p.mkdir(parents=True)
    (p / "jane.md").write_text("---\ntype: person\nid: jane\ntitle: Jane\n---\nbody\n")
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), reader=WorkspaceReader(str(tmp_path)),
    ))
    files = c.get("/api/workspace/tree", params={"subject": "u_jane"}).json()["files"]
    assert "kg/entities/person/jane.md" in files
    got = c.get("/api/workspace/file", params={"subject": "u_jane", "path": "kg/entities/person/jane.md"})
    assert got.status_code == 200 and "title: Jane" in got.json()["content"]
    assert c.get("/api/workspace/file", params={"subject": "u_jane", "path": "../../etc/passwd"}).status_code == 400
    assert c.get("/api/workspace/file", params={"subject": "u_jane", "path": "nope.md"}).status_code == 404


def test_workspace_upload_saves_hash_prefixed_files_under_subject(tmp_path):
    import hashlib

    from agent_api.workspace_reader import WorkspaceReader

    reader = WorkspaceReader(str(tmp_path))
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), reader=reader,
    ))
    first = b"one"
    second = b"two"
    r = c.post(
        "/api/workspace/upload",
        data={"subject": "u_jane"},
        files=[
            ("files", ("../../same.txt", first, "text/plain")),
            ("files", ("same.txt", second, "text/plain")),
        ],
    )

    assert r.status_code == 200
    first_name = f"{hashlib.sha256(first).hexdigest()[:16]}-same.txt"
    second_name = f"{hashlib.sha256(second).hexdigest()[:16]}-same.txt"
    files = r.json()["files"]
    assert files == [
        {"name": first_name, "path": f"uploads/{first_name}"},
        {"name": second_name, "path": f"uploads/{second_name}"},
    ]
    assert (tmp_path / "u_jane" / files[0]["path"]).read_bytes() == first
    assert (tmp_path / "u_jane" / files[1]["path"]).read_bytes() == second
    assert not (tmp_path / "same.txt").exists()


def test_workspace_tree_hidden_mode(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader
    ws = tmp_path / "u_jane"
    (ws / "kg").mkdir(parents=True)
    (ws / "kg" / "note.md").write_text("body\n")
    (ws / ".claude" / "sessions").mkdir(parents=True)
    (ws / ".claude" / "sessions" / "main.session").write_text("sess\n")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref\n")
    (ws / ".env").write_text("SECRET=1\n")

    reader = WorkspaceReader(str(tmp_path))

    # default: no dotfiles/dotdirs at all
    default = reader.tree("u_jane")
    assert default == ["kg/note.md"]

    # hidden=True: surfaces .claude + other dotfiles, but never .git internals
    shown = reader.tree("u_jane", hidden=True)
    assert ".claude/sessions/main.session" in shown
    assert ".env" in shown
    assert "kg/note.md" in shown
    assert not any(f.startswith(".git/") or f == ".git" for f in shown)

    # read() can open a hidden file (traversal-guard still applies)
    assert reader.read("u_jane", ".claude/sessions/main.session") == "sess\n"

    # endpoint passes the param through
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), reader=reader,
    ))
    plain = c.get("/api/workspace/tree", params={"subject": "u_jane"}).json()["files"]
    assert plain == ["kg/note.md"]
    with_hidden = c.get("/api/workspace/tree", params={"subject": "u_jane", "hidden": 1}).json()["files"]
    assert ".claude/sessions/main.session" in with_hidden


def _write_transcript(ws, sid: str, lines: list[dict]) -> None:
    import json
    proj = ws / ".claude" / "projects" / "-some-cwd-slug"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in lines))


def test_session_history_parses_turns(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader

    ws = tmp_path / "u_jane"
    (ws / ".claude" / "sessions").mkdir(parents=True)
    (ws / ".claude" / "sessions" / "main.session").write_text("sid-1\n")
    _write_transcript(ws, "sid-1", [
        {"type": "mode", "mode": "default"},                                # meta — skip
        {"type": "user", "message": {"role": "user", "content": "research DTCC"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm"},                        # ignored
            {"type": "text", "text": "Looking it up. "},
            {"type": "tool_use", "name": "Read", "input": {}},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [          # tool round-trip — same agent turn
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Done."},
            {"type": "tool_use", "name": "Grep", "input": {}},
        ]}},
        {"type": "user", "message": {"role": "user", "content": "thanks"}},
        "{ this is not valid json",                                          # tolerant — skipped
    ])

    reader = WorkspaceReader(str(tmp_path))
    turns = reader.history("u_jane", "main")

    assert [t["role"] for t in turns] == ["user", "agent", "user"]
    assert turns[0] == {"role": "user", "text": "research DTCC"}
    # the two assistant lines (split by a tool_result round-trip) fold into ONE agent turn
    assert turns[1]["text"] == "Looking it up. Done."
    assert [o["label"] for o in turns[1]["ops"]] == ["read", "search"]
    assert turns[2]["text"] == "thanks"


def test_session_history_tolerant_of_missing(tmp_path):
    from agent_api.workspace_reader import WorkspaceReader

    reader = WorkspaceReader(str(tmp_path))
    # no workspace / no pointer / no transcript → empty, never raises
    assert reader.history("u_ghost", "main") == []
    assert reader.history("u_jane", "../escape") == []

    # endpoint never 500s and returns {turns: []}
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), reader=reader,
    ))
    r = c.get("/api/sessions/main/history", params={"subject": "u_ghost"})
    assert r.status_code == 200
    assert r.json() == {"turns": []}


# ── live SSE resume (the real-time transcript-loss fix) ────────────────────────────────────────────

def test_sse_cursor_encode_decode_roundtrip():
    from agent_api.api import _decode_sse_cursor, _encode_sse_cursor
    last = {"tc:meeting:m1": "12-0", "unit:agent-meet-m1:out": "5-0"}
    sid = _encode_sse_cursor(last, "tc:meeting:m1", "unit:agent-meet-m1:out")
    assert sid == "12-0|5-0"
    assert _decode_sse_cursor(sid) == ("12-0", "5-0")
    assert _decode_sse_cursor(None) == (None, None)          # fresh connect
    assert _decode_sse_cursor("-|-") == (None, None)         # nothing read yet on either stream
    assert _decode_sse_cursor("9-0|-") == ("9-0", None)
    # Resilience: a malformed Last-Event-ID must degrade to a fresh connect, never crash the SSE.
    assert _decode_sse_cursor("") == (None, None)            # empty header
    assert _decode_sse_cursor("garbage-no-pipe") == (None, None)   # no separator → fresh connect
    assert _decode_sse_cursor("-|5-0") == (None, "5-0")      # only the output stream was read


def _stream_client(fake_redis, monkeypatch):
    import redis
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
    ))


class _StreamRedis:
    """Records the xread resume cursor + which streams were seeded (xrevrange). Ends the SSE fast by
    returning a session_end then draining empty."""
    def __init__(self):
        self.seeded = []
        self.xread_last = None
        self._reads = 0

    def xrevrange(self, key, count=1):
        self.seeded.append(key)
        return []

    def xread(self, last, count=500, block=0):
        self._reads += 1
        if self.xread_last is None:
            self.xread_last = dict(last)
        if self._reads == 1:
            import json as _j
            return [("tc:meeting:m1", [("9-0", {"payload": _j.dumps({"type": "session_end"})})])]
        return []   # drain → ending → meeting-end → return


def test_sse_resumes_from_last_event_id_no_reseed(monkeypatch):
    """RECONNECT (the real-time transcript-loss fix): with a Last-Event-ID, the live feed resumes EXACTLY
    from the client's cursor and does NOT re-seed the bounded tail — so segments published in the gap are
    delivered, not skipped."""
    fr = _StreamRedis()
    c = _stream_client(fr, monkeypatch)
    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "m1", "session_uid": "m1"},
                  headers={"Last-Event-ID": "7-0|3-0"}) as r:
        assert r.status_code == 200
        _ = r.read()
    assert fr.xread_last["tc:meeting:m1"] == "7-0"          # resumed from the cursor, NOT "$"
    assert fr.xread_last["unit:agent-meet-m1:out"] == "3-0"
    assert "tc:meeting:m1" not in fr.seeded                 # transcript tail NOT re-seeded on resume


def test_sse_fresh_connect_seeds_and_tails(monkeypatch):
    """No Last-Event-ID (fresh connect): seed the bounded transcript tail, then live-tail from there."""
    fr = _StreamRedis()
    c = _stream_client(fr, monkeypatch)
    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "m1", "session_uid": "m1"}) as r:
        assert r.status_code == 200
        _ = r.read()
    assert "tc:meeting:m1" in fr.seeded                     # fresh connect DID seed the tail
    assert fr.xread_last["tc:meeting:m1"] == "$"            # then tails live from now
