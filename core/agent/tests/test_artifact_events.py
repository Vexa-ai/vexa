"""F41 — the panel follows the write, because the stream says a document now exists.

The founder watched the agent create a shared workspace and write its README while the right panel
sat on `_global/README.md`: the one document it had just made was the one thing not on screen.
Decision 18 says layout is a function of the chat's RECORD, so the fix is an event on the tool
result, not a guess in the client.

And F39 — the no-narration rule ships on every dispatch, not only on turns a link composed.
"""
from __future__ import annotations

import json

from llm.claude_code import _WRITER_TOOLS, _written_artifact, parse_stream_json
from worker.engine import voice_preamble


def _use(tool, args, cid="c1"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": args, "id": cid}]}})


def _result(cid="c1", err=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid, "is_error": err, "content": "ok"}]}})


def _events(lines):
    return list(parse_stream_json(iter(lines)))


def test_the_mcp_verb_names_its_workspace_and_path():
    assert _written_artifact("mcp__vexa__workspace_write",
                             {"path": "README.md", "slug": "aswf-dna-project-b7b2ee"}) == \
        ("aswf-dna-project-b7b2ee", "README.md")
    # an empty slug means the caller's OWN desk — the record resolves it, the stream does not guess
    assert _written_artifact("mcp__vexa__workspace_write", {"path": "kg/x.md"}) == ("", "kg/x.md")


def test_the_harness_tools_are_read_out_of_the_container_path():
    assert _written_artifact("Write", {"file_path": "/workspaces/126/kg/entities/person/a.md"}) == \
        ("126", "kg/entities/person/a.md")
    # outside the store, or a shape we do not recognise: NO tab. A tab on a guessed path opens a
    # page that can never load, which is worse than one document fewer.
    assert _written_artifact("Write", {"file_path": "/etc/passwd"}) is None
    assert _written_artifact("Write", {"file_path": "/workspaces/126"}) is None
    assert _written_artifact("Write", {}) is None


def test_a_successful_write_emits_the_artifact_after_its_result():
    evs = _events([_use("Write", {"file_path": "/workspaces/ws1/README.md"}), _result()])
    arts = [e for e in evs if e["type"] == "artifact"]
    assert arts == [{"type": "artifact", "workspace": "ws1", "path": "README.md", "focus": True}]
    # ordering matters: the result comes first, so a client that renders in order sees the step
    # finish and then the tab appear, not a tab for work still in flight
    assert [e["type"] for e in evs].index("tool-result") < [e["type"] for e in evs].index("artifact")


def test_a_FAILED_write_opens_nothing():
    evs = _events([_use("Write", {"file_path": "/workspaces/ws1/README.md"}), _result(err=True)])
    assert not [e for e in evs if e["type"] == "artifact"]


def test_a_non_writing_tool_opens_nothing():
    evs = _events([_use("Read", {"file_path": "/workspaces/ws1/README.md"}), _result()])
    assert not [e for e in evs if e["type"] == "artifact"]
    # and the vocabulary is explicit, so a future `write_transcript` cannot opt itself in
    assert "Read" not in _WRITER_TOOLS and "mcp__vexa__workspace_write" in _WRITER_TOOLS


def test_a_result_with_no_matching_call_is_not_attributed_to_an_earlier_write():
    # pop-either-way: a failed call must not leave an entry a later unrelated result matches
    evs = _events([_use("Write", {"file_path": "/workspaces/ws1/a.md"}, cid="c1"),
                   _result(cid="c1", err=True),
                   _result(cid="c1")])
    assert not [e for e in evs if e["type"] == "artifact"]


def test_the_no_narration_rule_ships_on_every_turn():
    v = voice_preamble()
    assert "SILENTLY" in v
    assert "never narrate your own tool use" in v
    # it must be unconditional — the composed-opening note was not, which is how a `+` chat
    # narrated its way through the middle of a conversation
    assert v.strip()
