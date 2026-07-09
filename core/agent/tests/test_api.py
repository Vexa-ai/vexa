"""Front-door L2 tests — the agent-api HTTP surface over fakes (no runtime, no claude needed).

Proves: /health is live; /invocations validates + dispatches (and 400s a bad envelope); /api/chat
spawns a now-dispatch and streams its Stream back as SSE; chat is an honest 501 with no relay wired.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from control_plane.api import create_app
from shared.config import load_settings
from control_plane.dispatch import Dispatcher

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
    """A StreamReader fake — yields the dispatch's UnitEvents (what redis XREAD would relay). Accepts the
    ``resume`` kwarg (the resumable-SSE contract) even though this bare fake ignores the cursor."""
    def read(self, unit_id, *, resume=None):
        yield {"type": "message-delta", "text": "hi"}
        yield {"type": "commit", "sha": "abc123"}


def _client(stream_reader=None) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), stream_reader=stream_reader,
    ))


# ── SSE ownership gate (P0) test helpers ──────────────────────────────────────────────────────────
# The live SSE feed now OWNER-SCOPES every request (subject_of → meeting-api ownership lookup) before it
# opens the redis stream. In L2 we inject a fake owner-lookup: a map of {(user_id, row_id): native}, and
# every /api/meeting/stream request carries an X-User-Id. `None` from the lookup == not-owned → 403.
def _fake_owner_lookup(owned: dict):
    """owned = {(user_id, str(meeting_id)): native_meeting_id}. Returns a create_app-compatible
    ``(user_id, meeting_id) -> dict | None`` — the meeting record when owned, else None."""
    def _lookup(user_id, meeting_id):
        nat = owned.get((str(user_id), str(meeting_id)))
        if nat is None:
            return None
        return {"id": int(meeting_id) if str(meeting_id).isdigit() else meeting_id,
                "native_meeting_id": nat, "user_id": user_id}
    return _lookup


def test_health_ok():
    r = _client().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_models_reports_chat_and_workspace_streaming_model(tmp_path):
    from control_plane.workspace_reader import WorkspaceReader

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
    from control_plane.workspace_reader import WorkspaceReader

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
    from control_plane.workspace_reader import WorkspaceReader

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


class _CursorReader:
    """A StreamReader fake that surfaces per-event Stream cursors (``(event, cursor)`` tuples) — what the
    RESUMABLE chat SSE emits as ``id:`` lines — and records the ``resume`` cursor it was asked to read from."""
    def __init__(self):
        self.resume_seen = "UNSET"

    def read(self, unit_id, *, resume=None):
        self.resume_seen = resume
        yield ({"type": "message-delta", "text": "hi"}, "5-0")
        yield ({"type": "turn-complete"}, "6-0")


def test_chat_sse_carries_resumable_ids():
    """The chat turn SSE is RESUMABLE like /api/meeting/stream: every event carries an ``id:`` = the unit
    output Stream cursor, so a dropped view reconnects with Last-Event-ID and resumes gaplessly (the
    cold-start 'No chat output arrived' false failure is a client that gave up on a stream it could resume)."""
    reader = _CursorReader()
    c = _client(reader)
    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    body = r.text
    assert "id: 5-0" in body and "id: 6-0" in body      # cursors surfaced for resume
    assert '"message-delta"' in body and '"turn-complete"' in body
    assert reader.resume_seen is None                   # a fresh connect resumes from nothing


def test_chat_resume_reattaches_without_a_second_dispatch():
    """A reconnect (Last-Event-ID present) RE-ATTACHES to the warm unit and resumes the read from the
    cursor — it does NOT dispatch a second turn (the worker completes regardless; resume only re-shows
    the missed output). Proven by: no new runtime spawn on the resume, and the cursor reaches the reader."""
    runtime = _FakeRuntime()
    reader = _CursorReader()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=reader,
    ))
    r1 = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert r1.status_code == 200
    spawned_after_first = len(runtime.spawned)
    assert spawned_after_first >= 1                      # the fresh turn dispatched (spawned the unit)

    r2 = c.post(
        "/api/chat",
        headers={"Last-Event-ID": "5-0"},
        json={"prompt": "hi", "subject": "u_jane", "session": "s1"},
    )
    assert r2.status_code == 200
    assert r2.headers["X-Unit-Id"] == "agent-u_jane-chat-s1"   # same warm unit re-attached
    assert reader.resume_seen == "5-0"                         # resumed from the client's cursor
    assert len(runtime.spawned) == spawned_after_first, \
        "resume re-dispatched a turn — a reconnect must re-attach to the warm unit, not run it twice"


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


def test_meeting_process_on_sets_desired_state_only(monkeypatch):
    """ADR 0027 (single dispatch arbiter): /api/meeting/process ON writes the opt-in flag and reports
    the resume point (the frozen cursor) — it NEVER dispatches. The watcher arms from the same cursor
    on the next segment; two dispatchers racing here (cursor vs tail) is how the backfill got lost."""
    import redis

    class FakeRedis:
        def __init__(self):
            self.kv = {"proc:meeting:m9:cursor": "37-0"}  # we cleaned up to 37-0 last time

        def set(self, k, v, ex=None):
            self.kv[k] = v
            if ex is not None:
                self.ttls = getattr(self, "ttls", {}); self.ttls[k] = ex

        def get(self, k):
            return self.kv.get(k)

        def delete(self, k):
            self.kv.pop(k, None)

    fake = FakeRedis()
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    runtime = _FakeRuntime()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), redis_url="redis://test",
    ))

    r = c.post("/api/meeting/process", json={"native_id": "m9", "on": True, "subject": "u_jane"})

    assert r.status_code == 202
    assert r.json()["resumed_from"] == "37-0"           # where the watcher's arm WILL resume
    assert fake.kv.get("proc:meeting:m9:on") == "1"     # desired state written
    assert runtime.spawned == []                        # NO dispatch from the endpoint — watcher's job


def test_meeting_process_no_cursor_reports_full_history(monkeypatch):
    """A never-processed meeting has no cursor ⇒ the resume point is 0-0 (the watcher will backfill the
    whole transcript once). Still no dispatch from the endpoint."""
    import redis

    class FakeRedis:
        kv: dict = {}

        def set(self, k, v, ex=None):
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
    assert runtime.spawned == []


def test_meeting_process_off_freezes_cursor(monkeypatch):
    """OFF clears the processing flag but LEAVES the cursor frozen for the next re-enable."""
    import redis

    class FakeRedis:
        def __init__(self):
            self.kv = {"proc:meeting:m9:on": "1", "proc:meeting:m9:cursor": "37-0"}

        def set(self, k, v, ex=None):
            self.kv[k] = v
            if ex is not None:
                self.ttls = getattr(self, "ttls", {}); self.ttls[k] = ex

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
        meeting_owner_lookup=_fake_owner_lookup({("u_owner", "abc"): "abc"}),
    ))

    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "abc", "session_uid": "abc"},
                  headers={"X-User-Id": "u_owner"}) as r:
        body = "".join(r.iter_text())

    assert r.status_code == 200
    assert '"text": "still recent"' in body
    assert '"text": "tail"' in body
    assert '"processed tail"' in body
    assert '"meeting-end"' in body
    # transcript/output resume from their seeded tails; the proc stream from 0-0 (full replay —
    # notes upsert by id client-side, and the whole processed view must render on connect).
    assert fake.first_xread == {
        "tc:meeting:abc": "9-0", "unit:agent-meet-abc:out": "4-0", "proc:meeting:abc": "0-0",
    }


def test_meeting_stream_relays_proc_notes_and_closes_on_view_end(monkeypatch):
    """ADR 0027: the SSE tails proc:meeting:{row} directly — baseline cleaned notes arrive as `note`
    events without waiting for an out-stream LLM beat — and it CLOSES on the worker's view_end
    marker (evidence of completion, P21), not on a quiet-poll guess. The final beat's post-
    session_end notes therefore reach the live view before meeting-end."""
    import json
    import redis

    class FakeRedis:
        def __init__(self):
            self.calls = 0

        def xrevrange(self, stream, count=1):
            return []

        def exists(self, key):
            return 1  # the copilot wrote — the close must WAIT for view_end, not a quiet poll

        def xread(self, streams, count=500, block=15000):
            self.calls += 1
            if self.calls == 1:
                return [
                    ("tc:meeting:42", [("5-0", {"payload": json.dumps({"type": "transcription", "segments": [{"speaker": "J", "text": "um hi", "start": 1, "segment_id": "s1"}]})})]),
                    ("proc:meeting:42", [("6-0", {"note": json.dumps({"id": "s1", "text": "Hi.", "speaker": "J"})})]),
                ]
            if self.calls == 2:
                return [("tc:meeting:42", [("7-0", {"payload": json.dumps({"type": "session_end"})})])]
            if self.calls == 3:
                # the final beat lands AFTER session_end — still relayed, then the marker
                return [("proc:meeting:42", [
                    ("8-0", {"note": json.dumps({"id": "s1", "text": "Hi, polished."})}),
                    ("9-0", {"type": "view_end", "cursor": "7-0"}),
                ])]
            return []  # the empty poll after the marker closes the stream

    fake = FakeRedis()
    monkeypatch.setattr(redis, "from_url", lambda *_args, **_kwargs: fake)
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
        meeting_owner_lookup=_fake_owner_lookup({("u_owner", "42"): "42"}),
    ))

    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "42", "session_uid": "42"},
                  headers={"X-User-Id": "u_owner"}) as r:
        body = "".join(r.iter_text())

    assert '"Hi."' in body                       # baseline note straight off the proc stream
    assert '"Hi, polished."' in body             # the final beat's note, AFTER session_end
    assert '"meeting-end"' in body
    assert fake.calls == 4                       # closed ON the marker — no 45s cap, no early cut


def test_workspace_read_and_traversal_guard(tmp_path):
    from control_plane.workspace_reader import WorkspaceReader
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

    from control_plane.workspace_reader import WorkspaceReader

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
    from control_plane.workspace_reader import WorkspaceReader
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
    from control_plane.workspace_reader import WorkspaceReader

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


def test_session_history_found_in_active_mount_dir(tmp_path):
    """THE 'chats list but don't load' bug: with Personal off, the worker cwd follows the active set,
    so a thread's continuity (pointer + transcript) lands under a SHARED workspace dir — which the
    subject-keyed reader never searched. extra_roots (the caller's mount dirs) must find it."""
    from control_plane.workspace_reader import WorkspaceReader

    shared = tmp_path / "aswf-dna-52bd7a93"
    (shared / ".claude" / "sessions").mkdir(parents=True)
    (shared / ".claude" / "sessions" / "chat-x.session").write_text("sid-9\n")
    _write_transcript(shared, "sid-9", [
        {"type": "user", "message": {"role": "user", "content": "what is dna"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A VFX app."}]}},
    ])
    reader = WorkspaceReader(str(tmp_path))
    # the mount dir as an explicit candidate loads the thread (no sweep needed)
    turns = reader.history("28", "chat-x", extra_roots=[str(shared)])
    assert [t["role"] for t in turns] == ["user", "agent"]
    # out-of-root candidates are dropped, never raised on
    assert reader.history("28", "chat-x", extra_roots=["/etc", str(shared)]) == turns


def test_session_history_sweeps_unmounted_strands(tmp_path):
    """A thread recorded under a workspace that is NO LONGER mounted (deactivated shared ws) must
    still load: the reader's last-resort sweep finds the pointer without any extra_roots."""
    from control_plane.workspace_reader import WorkspaceReader

    gone = tmp_path / "some-shared-ws"          # not passed as an extra root — unmounted
    (gone / ".claude" / "sessions").mkdir(parents=True)
    (gone / ".claude" / "sessions" / "chat-z.session").write_text("sid-7\n")
    _write_transcript(gone, "sid-7", [
        {"type": "user", "message": {"role": "user", "content": "stranded"}},
    ])
    reader = WorkspaceReader(str(tmp_path))
    assert reader.history("28", "chat-z") == [{"role": "user", "text": "stranded"}]


def test_session_history_prefers_the_system_anchor(tmp_path):
    """New turns anchor continuity in the PRIVATE SYSTEM tier (<root>/.system/<subject>) — chats are
    private and must not live on a shared cwd. The reader searches _system FIRST."""
    from control_plane.workspace_reader import WorkspaceReader

    sysws = tmp_path / ".system" / "28"
    (sysws / ".claude" / "sessions").mkdir(parents=True)
    (sysws / ".claude" / "sessions" / "chat-y.session").write_text("sid-2\n")
    _write_transcript(sysws, "sid-2", [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
    ])
    reader = WorkspaceReader(str(tmp_path))
    turns = reader.history("28", "chat-y")
    assert turns == [{"role": "user", "text": "hello"}]


def test_session_history_tolerant_of_missing(tmp_path):
    from control_plane.workspace_reader import WorkspaceReader

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


# ── live registry: liveness is EVIDENCE, not a latch (P21 — the stale-"live" server-side root) ──────

def test_live_registry_demotes_silent_entries(monkeypatch):
    """An entry is "live" only while segments flow (the watcher re-adds every ~2s batch). Past
    LIVE_SILENCE_TTL_SEC of silence, list() reports it stopped — even when the session_end frame was
    LOST (hot-reload racing the wire), the registry can't claim a live meeting forever. A fresh add
    re-earns liveness."""
    from control_plane.api import LIVE_SILENCE_TTL_SEC, _LiveMeetings
    from control_plane import api as api_mod

    t = {"now": 1000.0}
    monkeypatch.setattr(api_mod.time, "monotonic", lambda: t["now"])
    live = _LiveMeetings()
    live.add({"session_uid": "42", "meeting_id": "42", "title": "m"})
    assert live.list()[0]["status"] == "live"

    t["now"] += LIVE_SILENCE_TTL_SEC + 1          # the segment flow went silent past the TTL
    assert live.list()[0]["status"] == "stopped"  # demoted from evidence, no session_end needed

    live.add({"session_uid": "42", "meeting_id": "42", "title": "m"})  # segments flow again
    assert live.list()[0]["status"] == "live"     # liveness re-earned


# ── live SSE resume (the real-time transcript-loss fix) ────────────────────────────────────────────

def test_sse_cursor_encode_decode_roundtrip():
    from control_plane.api import _decode_sse_cursor, _encode_sse_cursor
    last = {"tc:meeting:m1": "12-0", "unit:agent-meet-m1:out": "5-0", "proc:meeting:m1": "7-0"}
    sid = _encode_sse_cursor(last, "tc:meeting:m1", "unit:agent-meet-m1:out", "proc:meeting:m1")
    assert sid == "12-0|5-0|7-0"
    assert _decode_sse_cursor(sid) == ("12-0", "5-0", "7-0")
    assert _decode_sse_cursor(None) == (None, None, None)          # fresh connect
    assert _decode_sse_cursor("-|-|-") == (None, None, None)       # nothing read yet on any stream
    assert _decode_sse_cursor("9-0|-|-") == ("9-0", None, None)
    # PAD tolerance (ADR 0027 rollout): a pre-three-part id from an in-flight client decodes with
    # processed_id None — the SSE then replays the proc stream from 0-0 (idempotent client upsert).
    assert _decode_sse_cursor("12-0|5-0") == ("12-0", "5-0", None)
    assert _decode_sse_cursor("9-0|-") == ("9-0", None, None)
    # Resilience: a malformed Last-Event-ID must degrade to a fresh connect, never crash the SSE.
    assert _decode_sse_cursor("") == (None, None, None)            # empty header
    assert _decode_sse_cursor("garbage-no-pipe") == (None, None, None)   # no separator → fresh connect
    assert _decode_sse_cursor("-|5-0") == (None, "5-0", None)      # only the output stream was read


def _stream_client(fake_redis, monkeypatch):
    import redis
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
        meeting_owner_lookup=_fake_owner_lookup({("u_owner", "m1"): "m1"}),
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
                  headers={"Last-Event-ID": "7-0|3-0", "X-User-Id": "u_owner"}) as r:
        assert r.status_code == 200
        _ = r.read()
    assert fr.xread_last["tc:meeting:m1"] == "7-0"          # resumed from the cursor, NOT "$"
    assert fr.xread_last["unit:agent-meet-m1:out"] == "3-0"
    assert "tc:meeting:m1" not in fr.seeded                 # transcript tail NOT re-seeded on resume


def test_sse_fresh_connect_seeds_and_tails(monkeypatch):
    """No Last-Event-ID (fresh connect): seed the bounded transcript tail, then live-tail from there."""
    fr = _StreamRedis()
    c = _stream_client(fr, monkeypatch)
    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "m1", "session_uid": "m1"},
                  headers={"X-User-Id": "u_owner"}) as r:
        assert r.status_code == 200
        _ = r.read()
    assert "tc:meeting:m1" in fr.seeded                     # fresh connect DID seed the tail
    assert fr.xread_last["tc:meeting:m1"] == "$"            # then tails live from now


# ── SSE cross-tenant ownership regression (P0 — the FIX-FIRST blocker) ────────────────────────────
# The by-row-id SSE feed `/api/meeting/stream` must OWNER-SCOPE the row like the WS `/ws` path + the
# by-id REST path do. Pre-fix it read `tc:meeting:{meeting_id}` straight off the caller-supplied query
# param with NO identity/ownership check → any authenticated user B could enumerate A's rows and stream
# A's live transcript + copilot cards. These tests FAIL on the pre-fix code (the stream opened for B) and
# pass after: B is REFUSED (403, no stream opened) on A's row, and A's own row streams fine.
def _xtenant_stream_client(monkeypatch):
    """A live-SSE client whose owner-lookup says: row "10" is owned by u_alice (native "aaa-bbb-ccc"),
    row "20" is owned by u_bob (native "xxx-yyy-zzz"). The redis fake ends every stream immediately."""
    import redis

    class _Redis:
        def xrevrange(self, key, count=1):
            return []

        def xread(self, last, count=500, block=0):
            import json as _j
            # one session_end then drain → the SSE closes cleanly (so a REFUSAL is unambiguous: no body).
            if not getattr(self, "_done", False):
                self._done = True
                return [(f"tc:meeting:{list(last)[0].split(':')[-1]}",
                         [("9-0", {"payload": _j.dumps({"type": "session_end"})})])]
            return []

    fake = _Redis()
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    owned = {("u_alice", "10"): "aaa-bbb-ccc", ("u_bob", "20"): "xxx-yyy-zzz"}
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()), redis_url="redis://test",
        meeting_owner_lookup=_fake_owner_lookup(owned),
    ), raise_server_exceptions=True)


def test_sse_cross_tenant_meeting_stream_is_refused(monkeypatch):
    """B (u_bob) enumerates A's row (10) → 403, NO stream opened. B's OWN row (20) streams fine.
    A (u_alice) streams her own row (10). Missing identity → 401. This FAILS on the pre-fix code."""
    c = _xtenant_stream_client(monkeypatch)

    # B requests A's row → REFUSED before any stream opens.
    r = c.get("/api/meeting/stream", params={"meeting_id": "10", "session_uid": "aaa-bbb-ccc"},
              headers={"X-User-Id": "u_bob"})
    assert r.status_code == 403, "user B must NOT stream tenant A's live meeting"

    # No identity at all → fail closed. In the gateway-fronted topology (no default subject) that is a
    # 401; the L2 harness sets VEXA_AGENT_DEFAULT_SUBJECT (autouse `_default_subject`), so clear it here to
    # assert the real gateway contract: a missing X-User-Id is rejected, never a silent open.
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "")
    c_no_fallback = _xtenant_stream_client(monkeypatch)
    r = c_no_fallback.get("/api/meeting/stream", params={"meeting_id": "10", "session_uid": "aaa-bbb-ccc"})
    assert r.status_code == 401

    # B's OWN row streams fine.
    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "20", "session_uid": "xxx-yyy-zzz"},
                  headers={"X-User-Id": "u_bob"}) as r:
        assert r.status_code == 200
        _ = r.read()

    # A streams her OWN row fine.
    with c.stream("GET", "/api/meeting/stream", params={"meeting_id": "10", "session_uid": "aaa-bbb-ccc"},
                  headers={"X-User-Id": "u_alice"}) as r:
        assert r.status_code == 200
        _ = r.read()


def test_sse_owned_row_but_foreign_session_uid_is_refused(monkeypatch):
    """Defense-in-depth: B owns row 20, but pairs it with A's native as `session_uid` to sniff A's copilot
    out-stream (`unit:agent-meet-{session_uid}:out`) → 403. The session_uid must match the OWNED row."""
    c = _xtenant_stream_client(monkeypatch)
    r = c.get("/api/meeting/stream", params={"meeting_id": "20", "session_uid": "aaa-bbb-ccc"},
              headers={"X-User-Id": "u_bob"})
    assert r.status_code == 403


def test_workspace_init_seeds_from_template(tmp_path, monkeypatch):
    """Phase 6: POST /api/workspace/init seeds the subject's workspace from the validated template
    (the single seed primitive, surfaced as a control). Idempotent."""
    from control_plane.workspace_reader import WorkspaceReader
    seed = tmp_path / "seed"
    (seed / "agents").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    (seed / "agents" / "meeting.md").write_text("cfg\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))

    r = c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    assert r.status_code == 201
    assert r.json()["seeded"] is True
    ws = workspaces / "u_jane"
    assert (ws / ".git").exists()
    assert (ws / "CLAUDE.md").read_text() == "root\n"          # from the template
    assert (ws / "agents" / "meeting.md").exists()

    # init EAGERLY provisions the private _system tier too (identity + chats home), not just the baseline
    assert r.json()["system_seeded"] is True
    system_home = workspaces / ".system" / "u_jane"
    assert (system_home / ".git").exists()
    assert (system_home / "identity.md").exists()             # the light self-identity reference

    r2 = c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})   # idempotent (both tiers)
    assert r2.json()["already_initialized"] is True
    assert r2.json()["system_seeded"] is False


def test_workspace_swap_attaches_custom_repo_and_swaps_back(tmp_path, monkeypatch):
    """POST /api/workspace/swap clones a custom external git repo as the subject's active workspace
    (parking the seed), then swapping back to seed restores the parked tree. The store dir never
    surfaces as a subject. Real git over a local repo — no network."""
    import subprocess
    from control_plane.workspace_reader import WorkspaceReader

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))

    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "MARK").write_text("CUSTOM\n"); (origin / "CLAUDE.md").write_text("CUSTOM ROOT\n")
    run("add", "-A"); run("commit", "-q", "-m", "x")

    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    h = {"X-User-Id": "u_jane"}
    c.post("/api/workspace/init", headers=h)                      # seed the active workspace first

    r = c.post("/api/workspace/swap", headers=h, json={"repo": str(origin)})
    assert r.status_code == 200
    body = r.json()
    assert body["swapped"] is True and body["cloned"] is True and body["parked"] == "seed"
    assert (workspaces / "u_jane" / "MARK").read_text() == "CUSTOM\n"

    # the custom repo file is now visible in the workspace tree; the store dir is NOT a subject
    files = c.get("/api/workspace/tree", headers=h).json()["files"]
    assert "MARK" in files
    assert workspaces / ".attached"                                # parked under the dot-store

    back = c.post("/api/workspace/swap", headers=h, json={})       # repo omitted → swap back to seed
    assert back.json()["active"] == "seed" and back.json()["cloned"] is False
    assert (workspaces / "u_jane" / "CLAUDE.md").read_text() == "SEED\n"


def test_workspace_activate_adds_without_parking_then_deactivate_parks(tmp_path, monkeypatch):
    """POST /api/workspace/activate ADDS a repo to the active set without parking the private baseline;
    GET /api/workspace/active lists the ordered set; deactivate parks it; the baseline can be switched off."""
    import subprocess
    from control_plane.workspace_reader import WorkspaceReader

    seed = tmp_path / "seed"; seed.mkdir(); (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    origin = tmp_path / "origin"; origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("SHARED ROOT\n"); run("add", "-A"); run("commit", "-q", "-m", "x")

    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    h = {"X-User-Id": "u_jane"}
    c.post("/api/workspace/init", headers=h)

    r = c.post("/api/workspace/activate", headers=h, json={"repo": str(origin)})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] is True and body["cloned"] is True
    slug = body["slug"]
    # the private baseline was NOT parked — still the live tree
    assert (workspaces / "u_jane" / "CLAUDE.md").read_text() == "SEED\n"

    active = c.get("/api/workspace/active", headers=h).json()["active"]
    slugs = [m["slug"] for m in active]
    assert slugs[0] == "seed" and slug in slugs and len(active) == 2
    assert active[0]["primary"] is True and active[0]["role"] == "private"

    # deactivate parks it (dropped from the set, tree kept)
    d = c.post("/api/workspace/deactivate", headers=h, json={"slug": slug})
    assert d.status_code == 200 and d.json()["changed"] is True
    assert [m["slug"] for m in c.get("/api/workspace/active", headers=h).json()["active"]] == ["seed"]

    # the seed is a NORMAL workspace — switching it off leaves the set (empty now); its tree is untouched
    off = c.post("/api/workspace/deactivate", headers=h, json={"slug": "seed"})
    assert off.status_code == 200 and off.json()["changed"] is True
    assert c.get("/api/workspace/active", headers=h).json()["active"] == []
    assert (workspaces / "u_jane" / "CLAUDE.md").read_text() == "SEED\n"   # durable memory tree kept
    assert "seed" not in [s for s in c.get("/api/workspace/attached", headers=h).json()["active_set"]]
    # re-activating the seed switches it back on (home again)
    on = c.post("/api/workspace/activate", headers=h, json={"slug": "seed"})
    assert on.status_code == 200 and on.json()["changed"] is True
    assert [m["slug"] for m in c.get("/api/workspace/active", headers=h).json()["active"]] == ["seed"]


def test_workspace_new_creates_a_blank_workspace_and_adds_it_without_swapping(tmp_path, monkeypatch):
    """POST /api/workspace/new CREATES a fresh template-seeded workspace at a new slug and ADDS it to the
    active set (additive). It does NOT park/rebuild/back-up the baseline — the baseline stays live+primary."""
    from control_plane.workspace_reader import WorkspaceReader

    seed = tmp_path / "seed"; seed.mkdir(); (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    h = {"X-User-Id": "u_jane"}
    c.post("/api/workspace/init", headers=h)
    # write real work into the baseline so we can prove it is untouched
    (workspaces / "u_jane" / "kg").mkdir(parents=True, exist_ok=True)
    (workspaces / "u_jane" / "kg" / "real.md").write_text("MY REAL WORK")

    r = c.post("/api/workspace/new", headers=h, json={})
    assert r.status_code == 201
    body = r.json()
    assert body["changed"] is True and body["added"] is True and body["slug"] == "workspace-1"

    # the baseline is UNTOUCHED — not parked, not rebuilt
    assert (workspaces / "u_jane" / "kg" / "real.md").read_text() == "MY REAL WORK"
    assert not (workspaces / ".attached" / "u_jane" / "seed").exists()
    assert not (workspaces / ".attached" / "u_jane" / "seed-prev").exists()
    # the new workspace was seeded from the template into its slot and joined the set (checked/active)
    assert (workspaces / ".attached" / "u_jane" / "workspace-1" / "CLAUDE.md").read_text() == "SEED\n"
    active = c.get("/api/workspace/active", headers=h).json()["active"]
    slugs = [m["slug"] for m in active]
    assert slugs == ["seed", "workspace-1"]
    assert active[0]["primary"] is True                       # baseline still primary+first
    assert active[1]["primary"] is False and active[1]["repo"] is None

    # a named create honors the label; a second create mints a distinct slug
    r2 = c.post("/api/workspace/new", headers=h, json={"name": "Research"})
    assert r2.status_code == 201 and r2.json()["slug"] == "workspace-2"
    view = c.get("/api/workspace/attached", headers=h).json()
    assert view["slots"]["workspace-2"]["name"] == "Research"
    assert view["slots"]["workspace-1"]["name"] == "New workspace"


def test_workspace_publish_pushes_born_workspace_to_remote(tmp_path, monkeypatch):
    """POST /api/workspace/publish pushes the vexa-born active workspace's full history to the created
    (here: injected/local bare) repo. Per-call token, never stored; errors token-redacted (P15).
    Real git over a local bare repo — no network (the GitHub creation seam is monkeypatched)."""
    import subprocess
    from control_plane import workspace_publish as wp
    from control_plane.workspace_reader import WorkspaceReader

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))

    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main"], cwd=bare, check=True)
    monkeypatch.setattr(wp, "_github_create_repo", lambda name, private, token, org: str(bare))

    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    h = {"X-User-Id": "u_jane"}
    c.post("/api/workspace/init", headers=h)                       # a vexa-born workspace with history

    r = c.post("/api/workspace/publish", headers=h,
               json={"repo_name": "my-ws", "token": "ghp_SECRET"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["pushed_ref"]
    sha = subprocess.run(["git", "rev-parse", body["pushed_ref"]], cwd=bare,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert sha == body["head_sha"]
    # P15: the token never lands in the workspace's persisted git config
    assert "ghp_SECRET" not in (workspaces / "u_jane" / ".git" / "config").read_text()

    # bad input → 400 with a clear message; missing name → 400 too
    r2 = c.post("/api/workspace/publish", headers=h, json={"repo_name": "bad name!", "token": "t"})
    assert r2.status_code == 400
    r3 = c.post("/api/workspace/publish", headers=h, json={"token": "t"})
    assert r3.status_code == 400


def test_workspace_publish_slug_targets_that_workspace(tmp_path, monkeypatch):
    """POST /api/workspace/publish with `slug` publishes THAT workspace (own non-seed slot here) —
    the bare remote receives the slot's history, the seed workspace gains no publish remote, and an
    unknown slug is a 404 (resolved via _manage_dir, membership-checked)."""
    import subprocess
    from control_plane import workspace_publish as wp
    from control_plane.workspace_reader import WorkspaceReader

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))

    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main"], cwd=bare, check=True)
    monkeypatch.setattr(wp, "_github_create_repo", lambda name, private, token, org: str(bare))

    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    h = {"X-User-Id": "u_jane"}
    c.post("/api/workspace/init", headers=h)
    assert c.post("/api/workspace/new", headers=h, json={"name": "Acme"}).status_code == 201

    slot = workspaces / ".attached" / "u_jane" / "workspace-1"
    if not (slot / ".git").exists():  # ensure the slot is a committed git repo (publish needs history)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=slot, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=slot, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=slot, check=True)
    (slot / "kg").mkdir(exist_ok=True)
    (slot / "kg" / "acme.md").write_text("acme body\n")
    subprocess.run(["git", "add", "-A"], cwd=slot, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "acme content"], cwd=slot, check=True)

    r = c.post("/api/workspace/publish", headers=h,
               json={"repo_name": "acme-ws", "token": "ghp_SECRET", "slug": "workspace-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    slot_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=slot,
                               capture_output=True, text=True, check=True).stdout.strip()
    assert body["head_sha"] == slot_head                     # the SLOT's history landed, not the seed's
    assert "ghp_SECRET" not in (slot / ".git" / "config").read_text()   # P15 holds on the slug path too
    seed_cfg = workspaces / "u_jane" / ".git" / "config"
    assert "vexa-publish" not in (seed_cfg.read_text() if seed_cfg.exists() else "")  # seed untouched

    r404 = c.post("/api/workspace/publish", headers=h,
                  json={"repo_name": "x", "token": "t", "slug": "not-a-workspace"})
    assert r404.status_code == 404


def test_chat_accepts_context_bundle_and_folds_digest_into_prompt():
    """Context bundle (slice 1): /api/chat accepts ``context`` (no 422); when the surface gates
    the ambient digest ON, the schedule block reaches the dispatched worker's inline prompt
    (VEXA_START env) — server-derived rows via the injected schedule_source seam."""
    runtime = _FakeRuntime()
    rows = [{"id": 51, "status": "scheduled", "platform": "google_meet",
             "native_meeting_id": "abc-defg-hij",
             "data": {"title": "Acme intro", "scheduled_at": "2026-07-09T09:00:00Z"},
             "end_time": None, "start_time": None, "updated_at": None}]
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=_FakeReader(),
        schedule_source=lambda subject: rows,
    ))
    r = c.post("/api/chat", headers={"X-User-Id": "u_jane"}, json={
        "prompt": "what's my next meeting?", "session": "s-ctx",
        "context": {"tz": "UTC", "surface": {"list": "meetings"},
                    "unknown_future_field": {"x": 1}},   # extra=ignore: never a 422
    })
    assert r.status_code == 200
    import json as _json
    start = _json.loads(runtime.spawned[-1][2]["VEXA_START"])["entrypoint"]["inline"]
    assert "<schedule tz=" in start and '"Acme intro"' in start
    assert start.endswith("what's my next meeting?")


def test_chat_doc_surface_stays_lean_and_legacy_active_still_grounds():
    runtime = _FakeRuntime()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=_FakeReader(),
        schedule_source=lambda subject: [],
    ))
    # doc surface → no digest
    r = c.post("/api/chat", headers={"X-User-Id": "u_jane"}, json={
        "prompt": "hi", "session": "s-doc",
        "context": {"surface": {"list": "files", "tab": {"kind": "doc"}}},
    })
    assert r.status_code == 200
    assert "<schedule" not in runtime.spawned[-1][2]["VEXA_START"]  # JSON-encoded, substring still valid
    # legacy body (active only, no context) → prep grounding from client fields, as before
    r = c.post("/api/chat", headers={"X-User-Id": "u_jane"}, json={
        "prompt": "hi", "session": "s-legacy",
        "active": {"kind": "meeting", "native_id": "abc", "platform": "google_meet",
                   "status": "scheduled", "title": "Legacy"},
    })
    assert r.status_code == 200
    start = runtime.spawned[-1][2]["VEXA_START"]
    assert "PREPARE" in start and "Legacy" in start and "<schedule" not in start


# ── the 'Reconnecting' hang fixes (incident, user 28): attach-gap + no-cursor retry + keepalives ──

class _ChatFakeRedis:
    """Just enough redis for the chat path: the pre-dispatch tail snapshot (xrevrange), the turn-head
    record (set/get), and the chat-session index (_Sessions hset/expire/…) stubbed inert."""
    def __init__(self, tail=None, kv=None):
        self.tail = tail          # [(id, fields)] for unit:*:out xrevrange
        self.kv = dict(kv or {})
        self.ttls = {}

    def xrevrange(self, stream, count=1):
        return list(self.tail or []) if stream.startswith("unit:") else []

    def set(self, k, v, ex=None):
        self.kv[k] = v
        if ex is not None:
            self.ttls[k] = ex

    def get(self, k):
        return self.kv.get(k)

    def delete(self, k):
        self.kv.pop(k, None)

    # _Sessions surface (inert)
    def hset(self, *a, **k): pass
    def hgetall(self, *a, **k): return {}
    def sadd(self, *a, **k): pass
    def smembers(self, *a, **k): return set()
    def srem(self, *a, **k): pass
    def xrange(self, *a, **k): return []


def test_chat_fresh_turn_attaches_from_predispatch_tail(monkeypatch):
    """Fix A (attach gap): a fresh turn snapshots the out-Stream tail BEFORE dispatching and attaches the
    reader there — never at ``$`` — so events the worker writes between dispatch and attach (or a whole
    turn that finishes in the gap) are delivered, while PRIOR turns' events are not replayed."""
    import json as _json
    import redis

    fake = _ChatFakeRedis(tail=[("7-0", {})])
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    runtime = _FakeRuntime()
    reader = _CursorReader()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=reader,
        redis_url="redis://test",
    ))

    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1",
                                  "turn_id": "nonce-1"})

    assert r.status_code == 200
    assert reader.resume_seen == "7-0"          # attached from the snapshot, not "$"
    assert len(runtime.spawned) == 1            # the turn dispatched exactly once
    head = _json.loads(fake.kv["unit:agent-u_jane-chat-s1:turnhead"])
    assert head == {"turn_id": "nonce-1", "start": "7-0"}   # retry anchor recorded
    assert fake.ttls["unit:agent-u_jane-chat-s1:turnhead"] > 0


def test_chat_no_cursor_retry_reattaches_without_second_dispatch(monkeypatch):
    """Fix A (no-cursor reconnect): a retry that never saw an ``id:`` (no Last-Event-ID) but carries the
    SAME turn nonce re-attaches from the turn's recorded start — the missed events (including a terminal
    written while the client was gone) replay, and NO second turn is dispatched."""
    import json as _json
    import redis

    fake = _ChatFakeRedis(
        tail=[("9-0", {})],
        kv={"unit:agent-u_jane-chat-s1:turnhead": _json.dumps({"turn_id": "nonce-1", "start": "7-0"})},
    )
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    runtime = _FakeRuntime()
    reader = _CursorReader()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=reader,
        redis_url="redis://test",
    ))

    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1",
                                  "turn_id": "nonce-1"})

    assert r.status_code == 200
    assert reader.resume_seen == "7-0"          # replays from the turn's start, not the current tail
    assert runtime.spawned == []                # retry never dispatches a second turn


def test_chat_new_turn_with_stale_nonce_dispatches_fresh(monkeypatch):
    """A DIFFERENT nonce (a genuinely new turn — even with identical prompt text) must dispatch."""
    import json as _json
    import redis

    fake = _ChatFakeRedis(
        tail=[("9-0", {})],
        kv={"unit:agent-u_jane-chat-s1:turnhead": _json.dumps({"turn_id": "nonce-1", "start": "7-0"})},
    )
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    runtime = _FakeRuntime()
    reader = _CursorReader()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=reader,
        redis_url="redis://test",
    ))

    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1",
                                  "turn_id": "nonce-2"})

    assert r.status_code == 200
    assert reader.resume_seen == "9-0"          # fresh snapshot of the CURRENT tail
    assert len(runtime.spawned) == 1            # new turn dispatched
    head = _json.loads(fake.kv["unit:agent-u_jane-chat-s1:turnhead"])
    assert head["turn_id"] == "nonce-2"         # the retry anchor moved to the new turn


def test_chat_sse_emits_keepalive_comments():
    """Fix C: the reader's idle ticks (``None``) surface as SSE comment keepalives so proxies/clients
    never cut a byte-quiet stream during a long agent think."""
    class _IdleThenDone:
        def read(self, unit_id, *, resume=None):
            yield None
            yield None
            yield ({"type": "message-delta", "text": "hi"}, "5-0")
            yield ({"type": "turn-complete"}, "6-0")

    c = _client(_IdleThenDone())
    r = c.post("/api/chat", json={"prompt": "hi", "subject": "u_jane", "session": "s1"})
    assert r.status_code == 200
    assert r.text.count(": keepalive") == 2
    assert '"turn-complete"' in r.text


def test_redis_stream_reader_yields_keepalive_ticks(monkeypatch):
    """RedisStreamReader emits a ``None`` tick on every empty blocking read (the keepalive cadence) and
    still gives up after idle_giveup_ms with no events at all."""
    import redis as _redis
    from shared.adapters import RedisStreamReader

    class _QuietRedis:
        def xread(self, streams, count=50, block=15000):
            return []

    monkeypatch.setattr(_redis, "from_url", lambda *_a, **_k: _QuietRedis())
    reader = RedisStreamReader("redis://test", block_ms=10, idle_giveup_ms=30)
    out = list(reader.read("u1"))
    assert out == [None, None]                  # ticks until the giveup, then a clean end
