"""THE TOOLS DEFAULT TO WHERE THE CHAT IS WORKING (Vexa-ai/vexa#1611).

Founder, 2026-09-06, in a chat whose header chip read `personal` while the conversation was about a
customer's workspace: *"it creates files in the wrong workspace, we need so that the thing knew the
workspace of writing, if it's specified."*

The turn is told the target in words — that half is `core/agent/tests/test_chat_workspace_target.py`
— and the tools are pointed at it here, which is the half that has to be true even when the model
does not read carefully. `entity_upsert` and `workspace_write` with no `slug` go to the chat's
target; anything the model names wins, including the word for the desk; and the default is a
DEFAULT, so the isolation set still refuses what it always refused.
"""
from __future__ import annotations

import json

from conftest import as_user, tool
import vexa_control_mcp as rig


TARGET = "oenb-4040f6"


def _write(**kw):
    return json.loads(tool("workspace_write")(path="notes.md", content="hello", **kw))


def _upsert(**kw):
    return json.loads(tool("entity_upsert")(kind="company", name="OeNB", facts=["a fact"],
                                            source="the meeting", **kw))


def _bodies(http, frag):
    return [c["body"] for c in http.calls if frag in c["url"]]


# ── the default ──────────────────────────────────────────────────────────────────────────────────

def test_a_write_with_no_slug_lands_in_the_chats_target(monkeypatch):
    http = as_user(monkeypatch, "7", routes={"/api/workspace/file": (200, {"ok": True})})
    rig.CALL_TARGET.set(TARGET)
    _write()
    assert _bodies(http, "/api/workspace/file")[0]["slug"] == TARGET


def test_an_entity_with_no_slug_lands_in_the_chats_target(monkeypatch):
    """The founder's own example of the failure: he asked for a company page and the agent said it
    would *"seed it via entity_upsert, which writes into the target workspace"* — and it did not."""
    http = as_user(monkeypatch, "7", routes={"/api/workspace/entity": (200, {"path": "kg/x.md"})})
    rig.CALL_TARGET.set(TARGET)
    _upsert()
    assert _bodies(http, "/api/workspace/entity")[0]["slug"] == TARGET


def test_with_no_target_the_desk_is_still_the_default(monkeypatch):
    """Every chat that has chosen nothing, and every caller that is not a dispatched worker. This is
    the behaviour that must not change, and it is most of them."""
    http = as_user(monkeypatch, "7", routes={"/api/workspace/entity": (200, {})})
    _upsert()
    assert _bodies(http, "/api/workspace/entity")[0]["slug"] == ""


# ── what the model names wins ────────────────────────────────────────────────────────────────────

def test_a_named_workspace_beats_the_target_and_does_not_move_it(monkeypatch):
    """A single write elsewhere is an explicit ask — the founder's *"other workspaces still
    available to read and even to write, if explicit ask and purpose"*."""
    http = as_user(monkeypatch, "7", routes={"/api/workspace/file": (200, {})})
    rig.CALL_TARGET.set(TARGET)
    _write(slug="grp-ilm")
    assert _bodies(http, "/api/workspace/file")[0]["slug"] == "grp-ilm"
    assert rig.CALL_TARGET.get() == TARGET, "one write elsewhere is not a change of target"


def test_the_desk_can_still_be_named_from_a_chat_working_somewhere_else(monkeypatch):
    """*"note this on my desk"* — the other half of the rule, and the way this fix could have become
    a new failure. `workspaces()` has always called the desk "personal", so that is the word."""
    for word in ("personal", "desk"):
        http = as_user(monkeypatch, "7", routes={"/api/workspace/file": (200, {})})
        rig.CALL_TARGET.set(TARGET)
        _write(slug=word)
        # `workspace_write` omits the field entirely for the desk — its own long-standing spelling
        # of "" — so the assertion is that no workspace is named, not that "" is sent.
        assert _bodies(http, "/api/workspace/file")[0].get("slug", "") == ""


def test_reads_are_untouched(monkeypatch):
    """`slug=""` on a READ has always meant the person's own desk and nobody has ever been surprised
    by it. Widening the default to reads would change what "" means in two directions at once."""
    http = as_user(monkeypatch, "7", routes={"/api/workspace/tree": (200, {"files": []}),
                                             "/api/workspace/file": (200, {"content": "x"})})
    rig.CALL_TARGET.set(TARGET)
    tool("workspace_tree")()
    tool("workspace_read")(path="README.md")
    assert all(f"slug={TARGET}" not in u for u in http.urls("/api/workspace"))


# ── a default, never a grant ─────────────────────────────────────────────────────────────────────

def test_a_target_outside_a_scoped_dispatchs_isolation_set_is_refused(monkeypatch):
    """The scope is the CEILING and this is a DEFAULT — which is why the default is filled in BEFORE
    the scope check and is subject to it, rather than beside it where it would be a way past."""
    as_user(monkeypatch, "7")
    rig.CALL_SCOPE.set({"regime": "autonomous", "workspaces": ["team"]})
    rig.CALL_TARGET.set(TARGET)
    assert _write().get("refused") == "out_of_scope"


def test_a_target_inside_the_isolation_set_goes_through(monkeypatch):
    http = as_user(monkeypatch, "7", routes={"/api/workspace/file": (200, {})})
    rig.CALL_SCOPE.set({"regime": "autonomous", "workspaces": [TARGET]})
    rig.CALL_TARGET.set(TARGET)
    assert _write().get("refused") is None
    assert _bodies(http, "/api/workspace/file")[0]["slug"] == TARGET


def test_the_target_rides_the_delegation_token_and_not_the_scope(monkeypatch):
    """Where it comes from: agent-api mints it per dispatch. Beside `scope`, deliberately not inside
    it — a default stored where a permission lives becomes a grant the first time somebody reads it
    as one."""
    as_user(monkeypatch, "7")
    rig.CURRENT.set(None)
    tok = rig.DELEGATION_PREFIX + "x"
    monkeypatch.setattr(rig, "_is_delegation_token", lambda t: t == tok)
    monkeypatch.setattr(rig, "_verify_delegation", lambda t: {
        "sub": "7", "scope": {"regime": "human", "workspaces": "*"}, "target": TARGET})
    rig.CALL_TOKEN.set(tok)
    assert rig._subject() == "7"
    assert rig.CALL_TARGET.get() == TARGET
    assert TARGET not in json.dumps(rig.CALL_SCOPE.get())


# ── moving it ────────────────────────────────────────────────────────────────────────────────────

def test_the_verb_answers_with_the_slug_the_harness_turns_into_a_focus_event(monkeypatch):
    """The rig does not write the record — it has never been told which chat is calling. It confirms
    the person may write there and answers with the slug; `llm/claude_code._workspace_focus` turns
    that into the `focus` event agent-api reads on the way past. One writer."""
    as_user(monkeypatch, "7", routes={"/api/workspace/shared": (200, {"memberships": [
        {"workspace_id": TARGET, "role": "owner"}]})})
    out = json.loads(tool("workspace_target")(slug=TARGET))
    assert out["targeted"] == TARGET and out["role"] == "owner"
    assert "say" in out, "the person asked for a change of where their work lands — they are told"


def test_a_workspace_they_can_only_read_is_not_a_place_their_work_can_land(monkeypatch):
    as_user(monkeypatch, "7", routes={"/api/workspace/shared": (200, {"memberships": [
        {"workspace_id": TARGET, "role": "viewer"}]})})
    out = json.loads(tool("workspace_target")(slug=TARGET))
    assert out["refused"] == "read_only" and "targeted" not in out


def test_a_workspace_they_are_not_in_is_refused_by_name(monkeypatch):
    as_user(monkeypatch, "7", routes={"/api/workspace/shared": (200, {"memberships": []})})
    out = json.loads(tool("workspace_target")(slug=TARGET))
    assert out["refused"] == "not_yours" and "targeted" not in out


def test_the_empty_slug_puts_the_work_back_on_their_own_desk(monkeypatch):
    as_user(monkeypatch, "7")
    out = json.loads(tool("workspace_target")())
    assert out["targeted"] == "" and "desk" in out["workspace"]
