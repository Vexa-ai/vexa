"""A WORKSPACE CREATED FROM A CHAT JOINS THAT CHAT (Vexa-ai/vexa#1603).

Founder walk, 2026-09-06. In a desk chat he asked for *"a new workspace where we will collect
everything we know about ILM"*; the agent created the shared workspace `industrial-light-magic-…`.
He then asked to *"collect all the knowledge from all sources we have into this new one"*, and the
agent answered:

    *"The new workspace isn't in my native mount stack (it's reached via the workspace_* tools).
    Let me seed it via `entity_upsert`, which writes into the target workspace."*

    — *"not native workspace??"*

The rule his question states: creating a place IS the act of bringing it into the room. A
`workspace_create` in a turn puts the workspace in that chat's focus — the chip shows it, the panel
mounts it, and the agent's NEXT turn has it read-write like every other workspace in the focus. A
tool-only reach afterwards is the defect.

Three things have to be true for that, and they are the three sections below:

  1. the harness says a workspace was made, and says it only when one was;
  2. the session record holds the focus, additively, and raises the stale-mounts flag;
  3. the next turn's dispatch carries a mount generation that gets it a container with the new
     workspace bound read-write.

The client half — the chip and the panel — is pinned in
`clients/terminal/src/minutes/__tests__/workspaceFocus.test.ts`.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.api_shared import workspace_focus
from control_plane.dispatch import Dispatcher, _without_chat_session, build_mount_set
from control_plane.workspace_membership import InMemoryMembershipIndex, ensure_owner
from control_plane.workspace_attach import create_shared_workspace_dir
from control_plane.workspace_reader import WorkspaceReader
from llm.claude_code import _FOCUS_TOOLS, _workspace_focus, parse_stream_json
from worker import engine
from llm.openai_agent import _panel_events
from shared import units
from shared.config import load_settings


CREATED = {"created": "industrial-light-magic-4040f4", "name": "Industrial Light and Magic",
           "you_are": "owner"}


# ── 1. what the create says, and what it means ───────────────────────────────────────────────────

def test_a_successful_create_becomes_a_focus_event_naming_the_workspace():
    assert _workspace_focus(json.dumps(CREATED)) == {
        "type": "focus", "workspace": "industrial-light-magic-4040f4",
        "name": "Industrial Light and Magic"}


def test_the_event_claims_no_access_because_it_is_not_the_thing_that_knows():
    """Whether the next turn mounts this read-write is decided by membership, server-side. A harness
    that also asserted it would be a second answer to one question — and the wrong one to trust."""
    ev = _workspace_focus(json.dumps(CREATED))
    assert "write" not in ev and "mode" not in ev and "role" not in ev


def test_a_create_that_made_nothing_focuses_nothing():
    assert _workspace_focus(json.dumps({"error": "could not create that workspace"})) is None
    assert _workspace_focus(json.dumps({"created": ""})) is None
    assert _workspace_focus("not json") is None
    assert _workspace_focus(json.dumps(["a", "list"])) is None


def test_a_focus_aimed_at_a_guess_is_refused():
    # A slug is ONE path segment and never a dot-namespaced reserved one. This one is durable — it
    # survives into every later turn of the chat — so a malformed id is refused, never repaired.
    assert _workspace_focus(json.dumps({"created": "grp/../_global"})) is None
    assert _workspace_focus(json.dumps({"created": ".system"})) is None


def test_the_name_is_optional_and_never_invented():
    ev = _workspace_focus(json.dumps({"created": "grp-abc"}))
    assert ev == {"type": "focus", "workspace": "grp-abc"}


# ── the harness emits it after the tool result, and only on success ──────────────────────────────

def _use(tool, cid="c1"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": {"name": "ILM"}, "id": cid}]}})


def _result(payload, cid="c1", err=False):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid, "is_error": err, "content": body}]}})


def _events(lines):
    return list(parse_stream_json(iter(lines)))


def test_claude_code_emits_the_focus_after_its_tool_result():
    evs = _events([_use("mcp__vexa__workspace_new"), _result(CREATED)])
    assert [e["type"] for e in evs] == ["tool-call", "tool-result", "focus"]
    assert evs[-1]["workspace"] == "industrial-light-magic-4040f4"


def test_a_failed_create_moves_nothing():
    evs = _events([_use("mcp__vexa__workspace_new"), _result(CREATED, err=True)])
    assert [e["type"] for e in evs] == ["tool-call", "tool-result"]


def test_reading_or_writing_a_workspace_is_not_joining_one():
    """The vocabulary is closed for the reason `_WRITER_TOOLS` is: a prefix match on "workspace"
    would put every listing and every read into somebody's focus."""
    for tool in ("mcp__vexa__workspace_tree", "mcp__vexa__workspace_write",
                 "mcp__vexa__workspace_purpose", "mcp__vexa__workspaces"):
        assert tool not in _FOCUS_TOOLS


def test_the_worker_is_allowed_to_call_it():
    # A tool the worker's allow-set omits is a tool the model is never offered, and the whole fix
    # would be inert with everything else in place.
    import json as _json
    from pathlib import Path
    manifest = _json.loads(
        (Path(__file__).resolve().parents[1] / "worker" / "mcp_tools.v1.json").read_text())
    assert "workspace_new" in manifest["tools"]


def test_openai_agent_derives_the_same_event_from_the_same_result():
    """Both runners read one result through one function — a surface convention written twice is a
    convention that is right in one runner."""
    call = {"id": "c1", "name": "mcp__vexa__workspace_new", "args": {"name": "ILM"}}
    assert _panel_events(call, True, json.dumps(CREATED)) == [_workspace_focus(json.dumps(CREATED))]
    assert _panel_events(call, False, json.dumps(CREATED)) == []


# ── what agent-api reads off the stream ──────────────────────────────────────────────────────────

def test_the_focus_event_is_the_focus_and_nothing_else_is():
    assert workspace_focus({"type": "focus", "workspace": "grp-abc"}) == "grp-abc"
    # writing INTO a workspace is not joining it, and neither is opening a page in one
    assert workspace_focus({"type": "artifact", "workspace": "grp-abc", "path": "README.md"}) is None
    assert workspace_focus({"type": "open", "workspace": "grp-abc", "path": "README.md"}) is None
    assert workspace_focus({"type": "message-delta", "text": "grp-abc"}) is None
    assert workspace_focus("not an event") is None
    assert workspace_focus({"type": "focus", "workspace": "grp/../x"}) is None


# ── 2. the index holds it ────────────────────────────────────────────────────────────────────────

class _FakeRedis:
    """The primitives `_Sessions` uses, and nothing else."""

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.sets: dict[str, set] = {}

    def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.hashes.pop(key, None)


def _index_cases():
    """Both backings, every time — the in-memory fallback the unit tests run on and the redis hash
    production runs on. A field that lands in one and not the other is a focus that works on a
    developer's laptop only."""
    return [_Sessions(), _Sessions(_FakeRedis())]


def test_a_created_workspace_joins_the_chats_focus():
    for sess in _index_cases():
        sess.upsert("u1", "pchat-abc", title="a workspace for ILM")
        assert sess.add_workspace("u1", "pchat-abc", "grp-ilm") is True
        assert sess.list("u1")[0]["workspaces"] == ["grp-ilm"]


def test_it_is_ADDITIVE_because_the_turn_knows_one_member_not_the_set():
    """`upsert(workspaces=…)` RESTATES — a scaffolded turn knows the whole set. A create knows the
    one place it made, so restating from it would drop every other mount the chat had."""
    for sess in _index_cases():
        sess.upsert("u1", "s", workspaces=["_global", "u_priya"])
        sess.add_workspace("u1", "s", "grp-ilm")
        assert sess.list("u1")[0]["workspaces"] == ["_global", "u_priya", "grp-ilm"]


def test_creating_the_same_workspace_twice_changes_nothing():
    for sess in _index_cases():
        sess.add_workspace("u1", "s", "grp-ilm")
        assert sess.add_workspace("u1", "s", "grp-ilm") is False
        assert sess.list("u1")[0]["workspaces"] == ["grp-ilm"]


def test_the_focus_does_not_disturb_what_the_rail_already_reads():
    for sess in _index_cases():
        sess.upsert("u1", "s", title="First prompt", scaffold={"kind": "first-visit", "id": "SC1"},
                    touched=True, meeting="118")
        sess.add_workspace("u1", "s", "grp-ilm")
        row = sess.list("u1")[0]
        assert row["title"] == "First prompt" and row["touched"] is True
        assert row["scaffold"] == {"kind": "first-visit", "id": "SC1"} and row["meeting"] == "118"


# ── the stale-mounts semaphore: one value, one raiser, one lowerer ───────────────────────────────

def test_a_chat_that_never_made_a_workspace_keeps_the_unit_id_it_always_had():
    for sess in _index_cases():
        sess.upsert("u1", "s", title="hello")
        assert sess.mount_gen("u1", "s") == 0
        assert sess.take_mount_generation("u1", "s") == 0


def test_the_generation_steps_once_on_the_turn_AFTER_the_create():
    for sess in _index_cases():
        sess.upsert("u1", "s", title="a workspace for ILM")
        sess.add_workspace("u1", "s", "grp-ilm")
        # mid-turn the id must NOT move: the turn that made it is still streaming, and its own
        # reconnect has to find the unit it is watching.
        assert sess.mount_gen("u1", "s") == 0
        # the next FRESH turn takes it
        assert sess.take_mount_generation("u1", "s") == 1
        # …and the turn after that reuses the warm unit the first one spawned
        assert sess.take_mount_generation("u1", "s") == 1
        assert sess.mount_gen("u1", "s") == 1


def test_a_second_workspace_steps_it_again():
    for sess in _index_cases():
        sess.add_workspace("u1", "s", "grp-ilm")
        assert sess.take_mount_generation("u1", "s") == 1
        sess.add_workspace("u1", "s", "grp-vfx")
        assert sess.take_mount_generation("u1", "s") == 2


def test_a_row_older_than_the_field_simply_has_generation_zero():
    r = _FakeRedis()
    r.hashes["agent:session:u1:legacy"] = {"created": "1.0", "last_active": "2.0", "title": "old"}
    r.sets["agent:sessions:u1"] = {"legacy"}
    sess = _Sessions(r)
    assert sess.list("u1")[0]["mount_gen"] == 0
    assert sess.take_mount_generation("u1", "legacy") == 0


# ── the generation is what gets the next turn a different container ──────────────────────────────

def _inv(gen=None, session="pchat-abc"):
    ctx = {"kind": "none", "session": session}
    if gen:
        ctx["mount_gen"] = gen
    return units.make_dispatch(subject="175", trigger="message",
                               start=units.entrypoint(inline="hi"), context=ctx)


def test_generation_zero_is_byte_identical_to_the_id_the_chat_has_always_had():
    assert units.dispatch_id(_inv()) == "agent-175-chat-pchat-abc"
    assert units.dispatch_id(_inv(gen=0)) == "agent-175-chat-pchat-abc"


def test_a_stepped_generation_addresses_a_unit_nobody_has_spawned():
    """The runtime's create is an idempotent TOUCH that discards the spec env, so a warm worker
    keeps the mount table it booted with for the whole 15-minute window. A new id is how the next
    turn gets a container built with the new stack — and nothing is stopped to do it: the old worker
    finishes its background jobs and idles out on its own clock."""
    assert units.dispatch_id(_inv(gen=1)) == "agent-175-chat-pchat-abc-g1"
    assert units.dispatch_id(_inv(gen=1)) != units.dispatch_id(_inv())


def test_the_generation_never_reaches_the_sealed_wire_contract():
    """`context` is `additionalProperties: false`. `mount_gen` is an agent-api routing hint read off
    the in-memory dispatch, exactly like `context.session` beside it, and stripped before the check."""
    clean = _without_chat_session(_inv(gen=3))
    assert clean["context"] == {"kind": "none"}
    # …and the dispatch the id was derived from is untouched
    assert _inv(gen=3)["context"]["mount_gen"] == 3


# ── 3. and that container has the workspace, read-write ──────────────────────────────────────────

def test_the_next_turns_mount_set_carries_the_new_workspace_read_write(tmp_path):
    """The creator is its OWNER, so `shared_active_mounts` mounts it `write=True` — the role is
    re-read from the workspace's own `policy/members.json`, never from the index copy."""
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "175").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    wid = create_shared_workspace_dir(root, "Industrial Light and Magic")
    ensure_owner(root, wid, "175", index=InMemoryMembershipIndex())
    mounts = build_mount_set(settings, "175", [{"workspace_id": wid, "role": "owner"}])
    made = next((m for m in mounts if m["slug"] == wid), None)
    assert made is not None, "a workspace this chat created must be in the next turn's stack"
    assert made["write"] is True and made["role"] == "shared"
    # …BESIDE the stack, never instead of it: the organisation tier is still there and the
    # subject still has a writable private mount of their own.
    assert "_global" in {m["slug"] for m in mounts}
    assert any(m["role"] == "private" and m["write"] for m in mounts)


# ── the turn's own stream writes it ──────────────────────────────────────────────────────────────

INTERNAL = "internal-tier-secret-for-tests"

CREATE_TURN = [
    {"type": "message-delta", "text": "Making it now."},
    {"type": "focus", "workspace": "grp-ilm", "name": "Industrial Light and Magic"},
    {"type": "turn-complete"},
]


class _FakeRuntime:
    def __init__(self):
        self.spawned: list[str] = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(workload_id)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def __init__(self, events):
        self.events = events

    def read(self, unit_id, resume=None):
        yield from self.events


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    return {"root": root, "sessions": _Sessions(), "runtime": _FakeRuntime()}


def _client(stack, events):
    settings = load_settings(
        workspaces_dir=str(stack["root"]),
        global_system_workspace_path=str(stack["root"] / "_global"),
        internal_api_secret=INTERNAL,
        ui_url="https://app.example.test",
        redis_url="",
    )
    app = create_app(Dispatcher(settings, stack["runtime"], _FakeIdentity()),
                     stream_reader=_Reader(events),
                     reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"])
    return TestClient(app)


def _turn(client, session, prompt="a new workspace for everything we know about ILM"):
    return client.post("/api/chat", json={"prompt": prompt, "session": session},
                       headers={"X-User-Id": "u_priya"})


def test_a_turn_that_creates_a_workspace_focuses_it_on_THAT_chats_session(stack):
    client = _client(stack, CREATE_TURN)
    assert _turn(client, "pchat-abc").status_code == 200
    rows = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]
    assert rows[0]["session"] == "pchat-abc"
    assert rows[0]["workspaces"] == ["grp-ilm"]


def test_the_client_still_gets_every_event_it_always_got(stack):
    """A READ, not a reroute. The client updates the chip and mounts the panel off this same event;
    the index write is what makes the focus outlive this browser."""
    body = _turn(_client(stack, CREATE_TURN), "pchat-abc").text
    assert '"type": "focus"' in body and '"workspace": "grp-ilm"' in body
    assert "Making it now." in body and "turn-complete" in body


def test_the_NEXT_turn_of_that_chat_is_dispatched_to_a_fresh_unit(stack):
    """The whole point: the warm worker was spawned before the workspace existed and keeps its mount
    table, so the next turn has to be a different unit — and it is one, exactly once."""
    client = _client(stack, CREATE_TURN)
    _turn(client, "pchat-abc")
    quiet = _client(stack, [{"type": "turn-complete"}])
    _turn(quiet, "pchat-abc", prompt="now collect everything we know into it")
    _turn(quiet, "pchat-abc", prompt="and the meetings too")
    assert stack["runtime"].spawned == [
        "agent-u_priya-chat-pchat-abc",       # the turn that made it
        "agent-u_priya-chat-pchat-abc-g1",    # the turn after — a container with the new mount
        "agent-u_priya-chat-pchat-abc-g1",    # and the one after that reuses it
    ]


def test_a_turn_that_creates_nothing_focuses_nothing_and_moves_no_unit(stack):
    client = _client(stack, [{"type": "message-delta", "text": "hello"}, {"type": "turn-complete"}])
    _turn(client, "pchat-abc", prompt="hello")
    _turn(client, "pchat-abc", prompt="hello again")
    rows = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]
    assert rows[0]["workspaces"] == []
    assert stack["runtime"].spawned == ["agent-u_priya-chat-pchat-abc"] * 2


def test_the_focus_lands_on_the_session_that_CREATED_never_on_whatever_is_in_front(stack):
    client = _client(stack, CREATE_TURN)
    _turn(client, "pchat-maker")
    quiet = _client(stack, [{"type": "turn-complete"}])
    _turn(quiet, "pchat-bystander", prompt="something else")
    rows = {r["session"]: r for r in
            client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]}
    assert rows["pchat-maker"]["workspaces"] == ["grp-ilm"]
    assert rows["pchat-bystander"]["workspaces"] == []


def test_an_index_that_refuses_the_write_does_not_cost_the_person_their_turn(stack):
    """The focus is furniture; the turn is what they are waiting for."""
    class _Broken(_Sessions):
        def add_workspace(self, subject, session, workspace):
            raise RuntimeError("index down")

    stack["sessions"] = _Broken()
    r = _turn(_client(stack, CREATE_TURN), "pchat-abc")
    assert r.status_code == 200
    assert '"workspace": "grp-ilm"' in r.text and "turn-complete" in r.text


# ── and the same walk's other half: the bubble shows the person's words ──────────────────────────
#
#     *"Also seen in the same exchange: the 'Active context: the user is viewing the workspace file
#     README.md…' preamble the terminal prepends to a typed message renders inside the person's own
#     bubble; only the words after `---` are theirs."*
#
# The composer's own narration used to land on the HUMAN side of the only boundary marker there was:
# the server writes CONTEXT_SENTINEL in front of `body.prompt`, and `body.prompt` is the whole
# client-composed string. `human_half` cuts at the LAST sentinel, so the worker recorded the
# narration as the person's speech and the chat painted it back at them. The composer now writes its
# own marker between the two (`clients/terminal/src/surfaces/chat.tsx::promptWithActiveContext`);
# the client half is pinned in `clients/terminal/src/surfaces/__tests__/workspaceFocus.test.ts`.

TYPED = "collect all the knowledge from all sources we have into this new one"
NARRATION = ("Active context: the user is viewing the workspace file README.md. "
             "Read it with your Read tool if relevant.")
SERVER_GROUNDING = "## Your mounted workspaces\n\ntier list...\n\n"


def test_the_human_half_of_a_composed_turn_is_the_sentence_and_nothing_else():
    composed = (SERVER_GROUNDING + engine.CONTEXT_SENTINEL
                + NARRATION + "\n\n---\n" + engine.CONTEXT_SENTINEL + TYPED)
    assert engine.human_half(composed) == TYPED


def test_without_the_composers_own_marker_the_narration_IS_the_recorded_speech():
    """The defect, stated as a test so the fix cannot be undone by deleting one string. This is what
    the founder's bubble said."""
    old_shape = SERVER_GROUNDING + engine.CONTEXT_SENTINEL + NARRATION + "\n\n---\n" + TYPED
    assert engine.human_half(old_shape).startswith("Active context:")


def test_the_model_still_reads_the_narration_first_and_the_ask_last():
    """The prompt is unchanged in meaning — nothing was removed from what the agent is told, which
    is the whole reason the marker is a comment and not a deletion."""
    composed = (SERVER_GROUNDING + engine.CONTEXT_SENTINEL
                + NARRATION + "\n\n---\n" + engine.CONTEXT_SENTINEL + TYPED)
    assert composed.index(NARRATION) < composed.index(TYPED)
    assert composed.endswith(TYPED)
