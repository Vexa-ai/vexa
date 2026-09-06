"""A CHAT HAS A TARGET WORKSPACE: WRITES GO THERE (Vexa-ai/vexa#1611).

Founder walk, 2026-09-06 13:58Z. He was in a chat whose header chip read `personal` while the whole
conversation was about a customer's workspace, and the files landed on his desk:

    *"it creates files in the wrong workspace, we need so that the thing knew the workspace of
    writing, if it's specified. We have this "personal" and we probably should be able to set a
    workspace that we are targeting (other workspaces still available to read and even to write, if
    explicit ask and purpose)"*

The rule that states: a chat carries a TARGET beside its mount set. `workspaces[]` is what the chat
can REACH; `target` is where it WORKS, and they were one field until they disagreed on his screen.

Four things have to be true, and they are the four sections below:

  1. the record holds it — additively beside the focus, with the desk as the default, and the
     malformed refused rather than repaired;
  2. every turn's prompt NAMES it, by name, together with what is readable;
  3. the tools default to it — the turn's cwd is the target's mount, and the delegation token
     carries it so `entity_upsert`/`workspace_write` with no `slug` land there;
  4. the person and the agent can both move it, through ONE writer each side.

The client half — the chip, the rail row, the record — is pinned in
`clients/terminal/src/minutes/__tests__/workspaceTarget.test.tsx`.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.api_shared import _is_slug, target_preamble, workspace_focus
from control_plane.dispatch import (Dispatcher, _worker_cwd, build_mount_set,
                                    build_unit_env)
from control_plane.workspace_attach import create_shared_workspace_dir
from control_plane.workspace_membership import InMemoryMembershipIndex, ensure_owner
from control_plane.workspace_reader import WorkspaceReader
from llm.claude_code import _FOCUS_TOOLS, _workspace_focus, parse_stream_json
from shared import delegation, units
from shared.config import load_settings
from worker import engine


# ── 1. the record ────────────────────────────────────────────────────────────────────────────────

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
    production runs on. A field that lands in one and not the other is a target that works on a
    developer's laptop only."""
    return [_Sessions(), _Sessions(_FakeRedis())]


def test_a_chat_with_no_target_writes_to_the_persons_own_desk():
    """The DEFAULT, and it is an absence rather than a second spelling of "personal": every record
    written before this field has it, and "no target" and "the desk" must be one state or two halves
    of the merge would each have a different answer for the same chat."""
    for sess in _index_cases():
        sess.upsert("u1", "pchat-abc", title="hello")
        assert sess.target("u1", "pchat-abc") == ""
        assert sess.list("u1")[0]["target"] is None


def test_setting_the_target_records_it_and_says_it_changed():
    for sess in _index_cases():
        sess.upsert("u1", "s", workspaces=["oenb-4040f6"])
        assert sess.set_target("u1", "s", "oenb-4040f6") is True
        assert sess.target("u1", "s") == "oenb-4040f6"
        assert sess.list("u1")[0]["target"] == "oenb-4040f6"


def test_setting_the_same_target_twice_changes_nothing():
    """A REAL CHANGE ONLY — the flag it raises costs a container cold start, so a re-selection of
    what is already in force must not."""
    for sess in _index_cases():
        sess.set_target("u1", "s", "oenb-4040f6")
        assert sess.set_target("u1", "s", "oenb-4040f6") is False


def test_the_empty_string_puts_the_writes_back_on_the_desk():
    for sess in _index_cases():
        sess.set_target("u1", "s", "oenb-4040f6")
        assert sess.set_target("u1", "s", "") is True
        assert sess.target("u1", "s") == ""
        assert sess.list("u1")[0]["target"] is None
        # …and clearing what is already clear is not a change either
        assert sess.set_target("u1", "s", "") is False


def test_a_target_aimed_at_a_guess_is_refused():
    """Durable, and it decides where every later turn writes — so a malformed slug is refused, never
    repaired. The same shape check `workspace_focus` applies, from the same function."""
    for sess in _index_cases():
        assert sess.set_target("u1", "s", "grp/../_global") is False
        assert sess.set_target("u1", "s", ".system") is False
        assert sess.target("u1", "s") == ""
    assert _is_slug("oenb-4040f6") is True
    assert _is_slug("a/b") is False and _is_slug(".system") is False and _is_slug("") is False


def test_the_target_is_a_DIFFERENT_question_from_the_mount_set():
    """`workspaces` is reach, `target` is where the work lands. Moving one never moves the other —
    which is the whole distinction the founder's sentence draws."""
    for sess in _index_cases():
        sess.upsert("u1", "s", workspaces=["_global", "oenb-4040f6", "grp-ilm"])
        sess.set_target("u1", "s", "oenb-4040f6")
        row = sess.list("u1")[0]
        assert row["workspaces"] == ["_global", "oenb-4040f6", "grp-ilm"]
        assert row["target"] == "oenb-4040f6"
        sess.add_workspace("u1", "s", "grp-vfx")
        assert sess.list("u1")[0]["target"] == "oenb-4040f6", "adding a mount is not moving the work"


def test_moving_the_target_gets_the_next_turn_a_fresh_container():
    """THE SAME STALE-MOUNTS SEMAPHORE `add_workspace` raises, and it is needed for a different
    fact: the mount SET does not change when a target moves, but the turn's cwd and the delegation
    token's default `slug` are baked into the container at spawn. A warm worker keeps both for its
    whole 15-minute window — so without this the chip would move and the writes would not."""
    for sess in _index_cases():
        sess.upsert("u1", "s", workspaces=["oenb-4040f6"])
        assert sess.take_mount_generation("u1", "s") == 0
        sess.set_target("u1", "s", "oenb-4040f6")
        assert sess.mount_gen("u1", "s") == 0, "mid-turn the id must not move under a live stream"
        assert sess.take_mount_generation("u1", "s") == 1
        assert sess.take_mount_generation("u1", "s") == 1, "the turn after reuses the warm unit"


def test_a_row_older_than_the_field_simply_has_no_target():
    r = _FakeRedis()
    r.hashes["agent:session:u1:legacy"] = {"created": "1.0", "last_active": "2.0", "title": "old"}
    r.sets["agent:sessions:u1"] = {"legacy"}
    sess = _Sessions(r)
    assert sess.list("u1")[0]["target"] is None
    assert sess.target("u1", "legacy") == ""


def test_the_target_does_not_disturb_what_the_rail_already_reads():
    for sess in _index_cases():
        sess.upsert("u1", "s", title="First prompt", scaffold={"kind": "first-visit", "id": "SC1"},
                    touched=True, meeting="118")
        sess.set_target("u1", "s", "oenb-4040f6")
        row = sess.list("u1")[0]
        assert row["title"] == "First prompt" and row["touched"] is True
        assert row["scaffold"] == {"kind": "first-visit", "id": "SC1"} and row["meeting"] == "118"


# ── 2. every turn's prompt names it ──────────────────────────────────────────────────────────────

def test_the_line_is_the_founders_sentence():
    """Verbatim from the issue, because the whole point is that the agent is TOLD rather than left
    to infer — *"how to softly reinforce that?"* was answered with context, not a rule to repeat."""
    out = target_preamble("OeNB", ["Priya's desk", "ILM"])
    assert ("target workspace: OeNB — writes go here unless asked otherwise; "
            "Priya's desk, ILM are mounted to read; write there only on an explicit ask with its "
            "purpose.") in out


def test_with_nothing_else_mounted_the_read_clause_is_dropped():
    """A sentence about an empty set reads as a defect. Most chats are this case."""
    out = target_preamble("Priya's desk")
    assert "target workspace: Priya's desk — writes go here unless asked otherwise." in out
    assert "mounted to read" not in out


def test_the_line_names_workspaces_never_slugs():
    """#1585/#1602's rule. The caller resolves the names; this function does no lookup, which is
    exactly why it cannot accidentally render an id."""
    out = target_preamble("Austrian National Bank", ["Priya's desk"])
    assert "oenb-4040f6" not in out and "Austrian National Bank" in out


def test_the_target_is_never_also_listed_as_readable():
    """It is where writes go; saying it is also "mounted to read" would be true and useless, and it
    is the one name in the line that must not be ambiguous."""
    out = target_preamble("OeNB", ["OeNB", "ILM"])
    assert out.count("OeNB") == 1


def test_no_target_name_means_no_line_at_all():
    assert target_preamble("") == "" and target_preamble("   ", ["ILM"]) == ""


# ── 3. the tools default to it ───────────────────────────────────────────────────────────────────

def _settings(root):
    return load_settings(workspaces_dir=str(root),
                         global_system_workspace_path=str(root / "_global"),
                         internal_api_secret="s", ui_url="https://app.example.test", redis_url="")


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "175").mkdir(parents=True)
    return root


def test_the_turns_cwd_is_the_target_so_a_plain_Write_lands_there(store):
    """`Write` into the mounted target path is the natural default — the founder's failure was a
    turn whose cwd was his desk while the conversation was about a customer."""
    settings = _settings(store)
    wid = create_shared_workspace_dir(store, "Austrian National Bank")
    ensure_owner(store, wid, "175", index=InMemoryMembershipIndex())
    members = [{"workspace_id": wid, "role": "owner"}]

    without = build_mount_set(settings, "175", members)
    assert _worker_cwd(str(store), "175", without) == f"{store}/175", "the desk, as it always was"

    with_target = build_mount_set(settings, "175", members, target=wid)
    assert _worker_cwd(str(store), "175", with_target).endswith(wid)
    # …and NOTHING ELSE MOVED: the desk is still mounted, still writable, still readable.
    desk = next(m for m in with_target if m["path"] == f"{store}/175")
    assert desk["write"] is True and desk["primary"] is False


def test_a_target_that_is_not_a_writable_mount_moves_no_cwd(store):
    """Naming a slug is not a grant — the same rule the scaffold clause states one branch up. A
    read-only cwd is F59 and a cwd nobody asked for is decision 22; neither is bought here."""
    settings = _settings(store)
    mounts = build_mount_set(settings, "175", [], target="a-workspace-nobody-mounted")
    assert _worker_cwd(str(store), "175", mounts) == f"{store}/175"


def test_a_room_run_ignores_the_chats_target(store):
    """Decision 22 already decides that run's cwd. A chat's target must never be able to talk a room
    run into writing a desk — the failure Vexa-ai/vexa#1606 moved into the mount table."""
    settings = _settings(store)
    wid = create_shared_workspace_dir(store, "Austrian National Bank")
    ensure_owner(store, wid, "175", index=InMemoryMembershipIndex())
    mounts = build_mount_set(settings, "175", [{"workspace_id": wid, "role": "owner"}],
                             room={"meeting_id": "150", "ordered": []}, target=wid)
    assert not any(m.get("primary") for m in mounts)
    assert all(m["write"] is False for m in mounts
               if m.get("role") not in ("system",) and m["slug"] != "_global")


def _env(settings, *, target="", **kw):
    inv = units.make_dispatch(subject="175", trigger="message",
                              start=units.entrypoint(inline="hi"),
                              context={"kind": "none", "session": "pchat-abc"})
    return build_unit_env(settings, inv, unit_id="u1", token="t", target=target, **kw)


def test_the_worker_is_told_the_target_and_only_when_there_is_one(store):
    """A POSITIVE signal. `primary` also answers for a room run's group desk and for an ordinary
    subject's own baseline, so the worker cannot derive this from the mount shape."""
    settings = _settings(store)
    assert "VEXA_TARGET_WORKSPACE" not in _env(settings)
    assert _env(settings, target="oenb-4040f6")["VEXA_TARGET_WORKSPACE"] == "oenb-4040f6"


def test_the_delegation_token_carries_the_target_as_the_tools_default(store):
    """This is what makes `entity_upsert(...)` and `workspace_write(...)` with no `slug` land in the
    workspace the chat is working in. On the TOKEN rather than in an argument because the model must
    not have to remember it."""
    settings = _settings(store)
    settings = settings.model_copy(update={
        "mcp_url": "https://mcp.example.test/mcp",
        "mcp_delegation_secret": settings.mcp_delegation_secret.__class__("a-secret-for-tests"),
    })
    env = _env(settings, target="oenb-4040f6")
    claims = delegation.verify_delegation("a-secret-for-tests", env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["target"] == "oenb-4040f6"


def test_the_target_is_a_default_and_never_a_grant(store):
    """It sits BESIDE `scope`, deliberately not inside it: a scope is a ceiling, this is a default,
    and a default stored where a permission lives becomes a grant the first time somebody reads it
    as one."""
    tok = delegation.mint_delegation("k", subject="175", regime="human", target="oenb-4040f6")
    claims = delegation.verify_delegation("k", tok)
    assert claims["scope"] == {"regime": "human", "workspaces": "*"}
    assert "oenb-4040f6" not in json.dumps(claims["scope"])


def test_a_chat_with_no_target_mints_the_token_it_always_did():
    plain = delegation.verify_delegation("k", delegation.mint_delegation("k", subject="175"))
    assert "target" not in plain


def test_the_mount_stack_the_model_reads_marks_the_target():
    """agent-api says it in the person's vocabulary; this says it in the model's — which PATH a file
    operation belongs under. Two sentences, one fact, and neither derives it independently."""
    mounts = [{"slug": "_global", "path": "/workspaces/_global", "role": "global", "write": False},
              {"slug": "175", "path": "/workspaces/175", "role": "private", "write": True},
              {"slug": "oenb-4040f6", "path": "/workspaces/oenb-4040f6", "role": "shared",
               "write": True}]
    plain = engine.mounts_preamble(mounts)
    assert "this chat's target" not in plain

    marked = engine.mounts_preamble(mounts, "oenb-4040f6")
    line = next(ln for ln in marked.splitlines() if "/workspaces/oenb-4040f6" in ln)
    assert "this chat's target: writes go here unless asked otherwise" in line
    assert "/workspaces/175" in marked, "the desk is still declared — it is readable, not gone"
    assert "THE TARGET MARKED ABOVE WINS" in marked


# ── 4. moving it: one writer on each side ────────────────────────────────────────────────────────

TARGETED = {"targeted": "oenb-4040f6", "role": "owner"}
CREATED = {"created": "grp-ilm", "name": "Industrial Light and Magic"}


def test_the_verb_that_moves_the_target_emits_the_focus_event():
    """ONE VOCABULARY. A `focus` says *"this workspace is where this conversation is working"*, and
    that has always meant both halves — it is in the mount set, and it is where writes go. Two event
    kinds for one sentence is how a chip and a record come to disagree."""
    assert _workspace_focus(json.dumps(TARGETED)) == {"type": "focus", "workspace": "oenb-4040f6"}
    assert "mcp__vexa__workspace_target" in _FOCUS_TOOLS


def test_a_refused_target_moves_nothing():
    assert _workspace_focus(json.dumps({"refused": "read_only", "workspace": "oenb"})) is None
    assert _workspace_focus(json.dumps({"targeted": ""})) is None
    assert _workspace_focus(json.dumps({"targeted": "grp/../_global"})) is None


def test_the_harness_emits_it_after_the_tool_result_and_only_on_success():
    def _use(cid="c1"):
        return json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__vexa__workspace_target",
             "input": {"slug": "oenb-4040f6"}, "id": cid}]}})

    def _result(payload, err=False):
        return json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "c1", "is_error": err,
             "content": json.dumps(payload)}]}})

    ok = list(parse_stream_json(iter([_use(), _result(TARGETED)])))
    assert [e["type"] for e in ok] == ["tool-call", "tool-result", "focus"]
    assert ok[-1]["workspace"] == "oenb-4040f6"
    bad = list(parse_stream_json(iter([_use(), _result(TARGETED, err=True)])))
    assert [e["type"] for e in bad] == ["tool-call", "tool-result"]


def test_the_worker_is_allowed_to_call_it():
    """A tool the worker's allow-set omits is a tool the model is never offered, and the whole fix
    would be inert with everything else in place."""
    import pathlib
    manifest = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "worker" / "mcp_tools.v1.json").read_text())
    assert "workspace_target" in manifest["tools"]


# ── the turn's own stream writes it, and the prompt says so ──────────────────────────────────────

INTERNAL = "internal-tier-secret-for-tests"

TARGET_TURN = [
    {"type": "message-delta", "text": "Working in OeNB from now on."},
    {"type": "focus", "workspace": "oenb-4040f6", "name": "Austrian National Bank"},
    {"type": "turn-complete"},
]
QUIET_TURN = [{"type": "turn-complete"}]


class _FakeRuntime:
    def __init__(self):
        self.spawned: list[str] = []
        self.envs: list[dict] = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(workload_id)
        self.envs.append(env)
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
    app = create_app(Dispatcher(_settings(stack["root"]), stack["runtime"], _FakeIdentity()),
                     stream_reader=_Reader(events),
                     reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"])
    return TestClient(app)


def _turn(client, session, prompt="work in the OeNB workspace"):
    return client.post("/api/chat", json={"prompt": prompt, "session": session},
                       headers={"X-User-Id": "u_priya"})


def _sent_prompt(stack, n=-1):
    return json.loads(stack["runtime"].envs[n]["VEXA_START"])["entrypoint"]["inline"]


def test_a_turn_that_targets_a_workspace_records_it_on_THAT_chats_session(stack):
    client = _client(stack, TARGET_TURN)
    assert _turn(client, "pchat-abc").status_code == 200
    row = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"][0]
    assert row["session"] == "pchat-abc"
    assert row["target"] == "oenb-4040f6"
    # BOTH HALVES of one event: it is in the focus AND it is where writes go.
    assert row["workspaces"] == ["oenb-4040f6"]


def test_the_client_still_gets_every_event_it_always_got(stack):
    """A READ, not a reroute. The chip moves off this same event; the index write is what makes it
    outlive this browser."""
    body = _turn(_client(stack, TARGET_TURN), "pchat-abc").text
    assert '"type": "focus"' in body and '"workspace": "oenb-4040f6"' in body
    assert "Working in OeNB from now on." in body and "turn-complete" in body


def test_the_next_turn_is_told_the_target_and_dispatched_with_it(stack):
    client = _client(stack, TARGET_TURN)
    _turn(client, "pchat-abc")
    quiet = _client(stack, QUIET_TURN)
    _turn(quiet, "pchat-abc", prompt="collect everything we know about OeNB")
    assert "target workspace: oenb-4040f6 — writes go here unless asked otherwise" in _sent_prompt(stack)
    assert stack["runtime"].envs[-1]["VEXA_TARGET_WORKSPACE"] == "oenb-4040f6"


def test_the_persons_own_words_are_still_exactly_their_own_words(stack):
    """The line is MACHINERY and goes in front of the sentinel — F47's rule, and the reason the
    founder's bubble ever read `Active context: the u…` back at him."""
    client = _client(stack, QUIET_TURN)
    _turn(client, "pchat-abc", prompt="collect everything we know about OeNB")
    assert engine.human_half(_sent_prompt(stack)) == "collect everything we know about OeNB"


def test_a_chat_that_has_chosen_nothing_is_told_it_writes_to_the_desk(stack):
    """The default is STATED, not left implicit — the founder's failure was a chat that did not say
    where it writes, and silence is what that failure sounded like."""
    _turn(_client(stack, QUIET_TURN), "pchat-plain", prompt="hello")
    assert "target workspace: your own desk — writes go here unless asked otherwise" in _sent_prompt(stack)


def test_the_desk_stays_readable_from_a_chat_targeting_somewhere_else(stack):
    """*"note this on my desk"* has to keep working — a line naming only the target would teach the
    agent it has nowhere else to write, which is a new failure rather than a fix for the old one."""
    client = _client(stack, TARGET_TURN)
    _turn(client, "pchat-abc")
    quiet = _client(stack, QUIET_TURN)
    _turn(quiet, "pchat-abc", prompt="and note this on my desk")
    line = _sent_prompt(stack)
    head, sep, rest = line.partition(" — writes go here unless asked otherwise; ")
    assert sep and head.endswith("target workspace: oenb-4040f6")
    readable = rest.split(" are mounted to read")[0]
    # The desk is NAMED as readable — whatever the registry calls it — and is not the target.
    assert readable and "oenb-4040f6" not in readable
    assert "write there only on an explicit ask with its purpose" in line


def test_the_person_moves_it_by_clicking_a_chip(stack):
    """The header chip's route. The AGENT does not come through here — it emits a `focus` event, and
    `_binding_watch` writes it. One field, one writer per side."""
    client = _client(stack, QUIET_TURN)
    _turn(client, "pchat-abc", prompt="hello")
    r = client.post("/api/chat/target", json={"session": "pchat-abc", "workspace": ""},
                    headers={"X-User-Id": "u_priya"})
    assert r.status_code == 200 and r.json()["target"] is None


def test_a_workspace_this_person_cannot_write_is_refused_at_the_chip(stack):
    """Refused HERE rather than stored and discovered at the first write — the whole point of the
    field is that the agent may trust it."""
    client = _client(stack, QUIET_TURN)
    _turn(client, "pchat-abc", prompt="hello")
    r = client.post("/api/chat/target",
                    json={"session": "pchat-abc", "workspace": "somebody-elses-workspace"},
                    headers={"X-User-Id": "u_priya"})
    assert r.status_code == 403
    row = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"][0]
    assert row["target"] is None


def test_a_malformed_slug_is_a_400_not_a_stored_guess(stack):
    client = _client(stack, QUIET_TURN)
    _turn(client, "pchat-abc", prompt="hello")
    r = client.post("/api/chat/target", json={"session": "pchat-abc", "workspace": "grp/../_global"},
                    headers={"X-User-Id": "u_priya"})
    assert r.status_code == 400


def test_an_index_that_refuses_the_write_does_not_cost_the_person_their_turn(stack):
    """The target is furniture; the turn is what they are waiting for."""
    class _Broken(_Sessions):
        def set_target(self, subject, session, workspace):
            raise RuntimeError("index down")

    stack["sessions"] = _Broken()
    r = _turn(_client(stack, TARGET_TURN), "pchat-abc")
    assert r.status_code == 200
    assert '"workspace": "oenb-4040f6"' in r.text and "turn-complete" in r.text


def test_the_focus_event_still_means_what_it_meant():
    """Vexa-ai/vexa#1603's contract is untouched — reading a workspace is not joining one, and
    writing into one is not either."""
    assert workspace_focus({"type": "focus", "workspace": "oenb-4040f6"}) == "oenb-4040f6"
    assert workspace_focus({"type": "artifact", "workspace": "x", "path": "README.md"}) is None
    assert _workspace_focus(json.dumps(CREATED))["workspace"] == "grp-ilm"
