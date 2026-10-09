"""tool_events.py — what a harness's tool calls MEAN to the panel, shared by every harness.

A turn's tool results are summarised as ``tool-result`` UnitEvents by each adapter; a few of them
also move the person's screen: a write opens its file, a ``transcript_terms`` publish paints chips,
a bot send opens the live transcript, ``open_page`` opens what was asked for, and a workspace made
or targeted from the chat joins it. This module owns those closed vocabularies and the event each
successful result earns. ``claude_code``, ``codex`` and ``openai_agent`` import them from here, so
one turn paints the same screen whichever harness the deployment runs.
"""
from __future__ import annotations

import json


# Tools whose SUCCESS means a document now exists that the person should be looking at. The
# vocabulary is explicit rather than a prefix match: "a tool whose name contains write" would catch
# a future `workspace_write_policy` or a `write_transcript` and open tabs nobody asked for.
_WRITER_TOOLS = frozenset({
    "mcp__vexa__workspace_write",
    "Write",
    "Edit",
    "NotebookEdit",
})


# THE TRANSCRIPT-TERM PUBLISH (PRD decision 35). `transcript_terms` is the only tool whose SUCCESS
# is meant to paint something on the meeting view, so it is the only one whose RESULT BODY is read
# here rather than summarised. A closed vocabulary for the same reason `_WRITER_TOOLS` is one: a
# prefix match would let any future tool ending in `_terms` drive somebody's transcript.
_TERMS_TOOLS = frozenset({
    "mcp__vexa__transcript_terms",
    "transcript_terms",
})

# The send that puts a bot in a room NOW.
_BOT_TOOLS = frozenset({
    "mcp__vexa__request_meeting_bot",
    "request_meeting_bot",
})

# THE ASK TO OPEN SOMETHING (Vexa-ai/vexa#1586). Every other panel move on this page is a SIDE
# EFFECT of doing something else — a write opens its file, a send opens its transcript. This one is
# the act itself: the founder typed "open meeting transcript", the agent read the 677 segments and
# described them, and he answered *"it did not open the transcript"*. Asked to open something, the
# only move it had was to describe it.
#
# The tool is served by the vexa MCP rather than being a harness builtin, and that is what makes it
# work on BOTH runners from one implementation: `claude-code` drives a CLI whose tool list is the
# CLI's own and cannot reach a Python builtin (`llm/JOBS.md` states this for `spawn_job`), while an
# MCP verb is reachable by every runner that attaches the server. The event derivation below is
# shared the same way `_bot_artifact` is.
_OPEN_TOOLS = frozenset({
    "mcp__vexa__open_page",
    "open_page",
})


# A WORKSPACE MADE FROM THIS CONVERSATION JOINS IT (Vexa-ai/vexa#1603). The founder asked for *"a
# new workspace where we will collect everything we know about ILM"*, got one, and was then told
# *"the new workspace isn't in my native mount stack (it's reached via the workspace_* tools)"* —
# *"not native workspace??"*. Creating a place IS the act of bringing it into the room; reaching it
# through the tools afterwards is the defect. So the create emits its own event, exactly as a send
# emits the transcript's: the chip shows it, the panel mounts it, and agent-api reads the same
# event on the way past to put it in the session's focus for every later turn and every other
# browser.
#
# A CLOSED VOCABULARY, for the reason `_WRITER_TOOLS` is one: a prefix match on "workspace" would
# put every listing, read and purpose-edit into somebody's focus.
#
# …AND SO DOES THE VERB THAT MOVES THE TARGET (Vexa-ai/vexa#1611). `workspace_target` is what an
# agent calls when the person says *"work in the OeNB workspace"*; it emits the SAME event, because
# a `focus` says "this workspace is where this conversation is working" and that has always meant
# both halves — it is in the chat's mount set, and it is the one writes go to. Two event kinds for
# one sentence is how a chip and a record come to disagree.
_FOCUS_TOOLS = frozenset({
    "mcp__vexa__workspace_new",
    "workspace_new",
    "mcp__vexa__workspace_target",
    "workspace_target",
    "workspace_import",
    "mcp__vexa__workspace_import",
    "workspace_import_status",
    "mcp__vexa__workspace_import_status",
})


def _tool_result_text(content: object) -> str:
    """The tool result as one string, whichever shape the harness handed it in.

    Claude Code emits a tool result either as a bare string or as a list of content blocks; both
    reach here, and a reader that handles only one of them fails SILENTLY on the other — which for
    this seam means chips that simply never appear and nothing anywhere saying why."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _published_terms(content: object) -> "dict | None":
    """The `terms` event a `transcript_terms` result asks for, or None.

    ONLY WHEN THE AGENT PUBLISHED. The tool answers a bare look-up call with ``emit: []`` — that
    call was the agent reading the room, and painting its raw output would put every capitalised
    word in the meeting on the person's screen. An empty publish is a NON-EVENT rather than an empty
    event: an empty event would clear the chips the previous Highlight put there."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    emit = obj.get("emit")
    if not isinstance(emit, list) or not emit:
        return None
    return {"type": "terms", "meeting": str(obj.get("meeting") or ""),
            "cursor": str(obj.get("cursor") or ""), "terms": emit}


def _bot_artifact(content: object) -> "dict | None":
    """The panel move a successful `request_meeting_bot` earns, or None (F73, decision 30.4).

    The founder watched the agent finish a send and then offer him a LINK into the product he was
    already looking at. The fix is not a better sentence — the panel is the product's own surface and
    moving it is the harness's job, not something the model should be asked to remember. So the send
    itself opens the live transcript beside the chat.

    BY THE ROW, NEVER THE NATIVE ID. `path` is the literal string ``meeting:`` + the meeting row id;
    a personal room's native id spans every meeting ever held in it, so it names a series and the
    resolver would pick whichever occurrence is newest. The send answers with the meeting row it
    created (`id`) — or, for a bot already in that room, `{"status": "already_exists", "meeting":
    row}`. No row, no event — a panel aimed at a guess is the failure this whole seam is careful
    about, and a meeting whose bot already `failed` opens nothing.

    `pin` and `focus` are separate and both are wanted here: pin KEEPS the transcript in the strip so
    it survives the next thing opened, focus FRONTS it now.

    …AND THE NATIVE ID RIDES ALONG (Vexa-ai/vexa#1597). This event is the only place in the system
    where "a bot was sent, from THIS chat, into THAT meeting" is stated, and agent-api reads it to
    BIND the meeting to the chat's session. The row addresses the meeting as the panel addresses it;
    the native id is how everything that talks to meeting-api addresses it (`stop_bot`, the
    transcript API), so the binding carries both rather than making a second lookup the price of
    knowing the second one. Nothing renders it — the client already reads a native id off the
    meetings list — so an absent one costs the binding a field, never the event."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("status") == "already_exists":
        obj = obj.get("meeting") if isinstance(obj.get("meeting"), dict) else {}
    if str(obj.get("status") or "").lower() == "failed":
        return None
    row = str(obj.get("id") or "").strip()
    if not row:
        return None
    ev = {"type": "artifact", "path": f"meeting:{row}", "pin": True, "focus": True}
    native = str(obj.get("native_meeting_id") or "").strip()
    if native:
        ev["native"] = native
    return ev


def _workspace_focus(content: object) -> "dict | None":
    """The `focus` event a successful `workspace_new` or `workspace_target` earns, or None
    (Vexa-ai/vexa#1603, Vexa-ai/vexa#1611).

    ONE FIELD DECIDES IT: `created`, the workspace id the create route minted, which the tool
    returns for a create and for nothing else — or `targeted`, the id `workspace_target` returns
    for a workspace it confirmed this person can write. No id, no event — a focus aimed at a guess
    would put a chat permanently over a workspace that does not exist, the same failure
    `_bot_artifact` is careful about one object along.

    TWO NAMES, ONE FIELD, deliberately: the tools answer different questions ("I made this" versus
    "we are working here") and a result that said `created` for a target would be a lie in the
    transcript. What they MEAN to the chat is identical, which is why one event carries both.

    `name` rides along because this is the only moment the workspace's HUMAN name is in hand on
    this path; the client shows names, never slugs. It is display only and an absent one costs the
    event a field, never the focus.

    THE EVENT CLAIMS NO ACCESS. Whether the next turn mounts this read-write is decided by
    membership, server-side, in `shared_active_mounts` — not by a string a harness wrote. What this
    event says is only "this workspace is now part of this conversation", which is the one thing
    the harness is in a position to know."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("status") == "completed" and isinstance(obj.get("result"), dict):
        obj = {"targeted": obj["result"].get("workspace"), "name": obj["result"].get("name")}
    wid = str(obj.get("created") or obj.get("targeted") or "").strip()
    # A slug is one path segment and never a dot-namespaced reserved one. Same shape check the
    # store itself applies; refusing here keeps a malformed answer out of a durable session record.
    if not wid or "/" in wid or wid.startswith("."):
        return None
    ev = {"type": "focus", "workspace": wid}
    name = str(obj.get("name") or "").strip()
    if name:
        ev["name"] = name
    return ev


def _open_event(content: object) -> "dict | None":
    """The `open` event a successful `open_page` result asks for, or None (Vexa-ai/vexa#1586).

    The TOOL decides whether anything is there — it resolves the target against the person's own
    workspaces and meetings and answers `opened: false` with a reason when it is not — so this reads
    the answer rather than re-deriving it. A refusal paints nothing: the agent was told, in words,
    and its one-line reply is that reason.

    `path` carries the SAME two dialects an `artifact` event does, so the client resolves both
    through the one function it already has (`pageForArtifact`): a workspace-relative path for a
    document, and the literal `meeting:<row id>` for the live transcript canvas. A transcript is not
    a file (founder ruling 2026-09-01) and this is where that stays true.

    There is no `focus` flag and that is deliberate. An `artifact` is the turn saying "I wrote
    this"; an `open` is a person having asked to see it, so it always comes to the front — including
    over a page the reader opened moments ago, which for `artifact` is the case that must NOT
    interrupt them."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or not obj.get("opened"):
        return None
    path = str(obj.get("path") or "").strip()
    if not path:
        return None
    return {"type": "open", "target": str(obj.get("target") or ""),
            "workspace": str(obj.get("workspace") or ""), "path": path}


def _written_artifact(tool: str, args: dict) -> "tuple[str, str] | None":
    """`(workspace, path)` the call is about to write, or None. Read off the ARGUMENTS, at tool-use
    time, because the result carries only a summary string.

    Two dialects, because two kinds of tool write workspace files:
      * the MCP verb takes `path` (workspace-relative) and `slug` (empty = the caller's own desk);
      * the harness tools take an absolute container path under `/workspaces/<slug>/<rel>`.
    Anything else — a write outside the store, a shape we do not recognise — returns None and no
    tab is opened. A tab pointing at a path we guessed is worse than no tab: it opens a page that
    can never load, which is the failure the scaffold's `meeting:note` rule already names."""
    if tool == "mcp__vexa__workspace_write":
        rel = str(args.get("path") or "").strip().lstrip("/")
        slug = str(args.get("slug") or "").strip()
        return (slug, rel) if rel else None
    raw = str(args.get("file_path") or args.get("notebook_path") or "").strip()
    if not raw.startswith("/workspaces/"):
        return None
    rest = raw[len("/workspaces/"):]
    slug, _, rel = rest.partition("/")
    return (slug, rel) if slug and rel else None


def _short(content: object, n: int = 80) -> str:
    s = content if isinstance(content, str) else json.dumps(content, default=str)
    s = " ".join(s.split())
    return s[:n]
