"""EVERY TOOL A PRODUCT PROMPT NAMES IS ONE THE ONE MCP SERVES (ADR-0037 §2, ADR-0041).

A standard deployment has one MCP server, at the gateway, assembled from the manifests of the
domains that are deployed. The prompts the product ships — `behavior/` (the asks, the desk cards,
the post-meeting kick, the workspace seed), the strings the agent and flows compose into a turn, and
the terminal's fallback sentences — tell a model which tool to call by NAME. A name the edge does not
serve is not a smaller feature: the turn searches for a tool that is not there, improvises, or tells
the person the product cannot do something it can. The dogfood rig served a wider surface, and
prompts written against it named a dozen of its verbs that no customer deployment had.

THE SERVED SET is read from source, never restated here: the MCP service's own tools (each a route
whose `operation_id` is the tool's name) and every domain's `mcp.tools.v1.json` tool a full
deployment (identity, meetings, flows, agent) carries.

A REFERENCE is a tool-shaped mention of a name that is a tool somewhere — served, allow-listed, or
one of the verbs only the dogfood rig serves (`RIG_ONLY`, named here because nothing under `core/`
reads `deploy/`): `` `name` ``, `` `name(…`` , a bare call `name(…`, or `mcp__vexa__name`.
Plain English ("propose the first piece", "validate it") is not a reference, and docstrings and
comments are not prompts. Every reference must be to a served tool.

The worker's allow-list and the harness's result vocabularies are held to the same set, except
`ALLOWLIST_GAP`: two verbs the harness turns into panel and focus events that only the rig serves
today, which no product prompt names. That set is checked in both directions — it can only shrink.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
FULL_DEPLOYMENT = {"identity", "meetings", "flows", "agent"}
MANIFEST_GLOBS = ("core/*/mcp.tools.v1.json", "core/*/services/*/mcp.tools.v1.json")
BUILT_IN_SOURCE = REPO / "core/meetings/services/mcp/src/vexa_mcp/app.py"
ALLOWLIST = REPO / "core/agent/worker/mcp_tools.v1.json"
#: The verbs only the dogfood rig serves — the surface product prompts drifted onto. A prompt naming
#: one of these on a standard deployment names a tool that is not there.
RIG_ONLY = frozenset({
    "auth_claim", "auth_link", "bot_config", "bot_say", "bot_schedule", "bot_send", "bot_stop",
    "bots_running", "captions_to_segments", "company_context", "confirm_login", "deeplink",
    "fact_emit", "friction_dump", "friction_fixed", "mark_scaffolded", "meeting_delete",
    "meeting_info", "meeting_participants", "meeting_seed", "meeting_transcript", "meeting_update",
    "meetings_list", "open_page", "recordings_list", "rehearse", "rehearse_states", "settings",
    "start_onboarding", "subject_reset", "transcript_search", "user_ensure", "vexa_overview",
    "vexa_search_docs", "workspace_attach", "workspace_init", "workspace_pull", "workspace_push",
    "workspace_regime", "workspace_target", "zoom_transcript_to_segments",
})

#: Allow-listed and harness-consumed verbs the edge does not serve yet (Vexa-ai/vexa#1586, #1611).
ALLOWLIST_GAP = frozenset({"open_page", "workspace_target"})

#: Where the product's prompts live.
PROMPT_TREES = ("behavior",)
PROMPT_TEXT_GLOBS = ("core/agent/shared/*.txt",)
PROMPT_CODE_TREES = ("core/agent", "core/flows/src")
#: The terminal's fallback sentences for an instance whose preset library predates an ask.
CLIENT_PROMPT_FILES = ("clients/terminal/src/minutes/extend.ts",)
_SKIP_PARTS = {"tests", "__tests__", "eval", ".venv", "node_modules", "__pycache__"}

#: The harness's own spelling of a vexa tool — a reference wherever it appears.
_QUALIFIED = re.compile(r"mcp__vexa__([a-z][a-z0-9_]*)")
_REFERENCE = re.compile(
    r"`([a-z][a-z0-9_]*)(?=`|\()"              # `name` or `name(…`
    r"|(?<![\w.`/-])([a-z][a-z0-9_]*)\(")      # a bare call: name(…
#: A string that IS one identifier is a name in a table (a harness vocabulary, a verb map), not
#: text a turn is sent; those tables are held to the served set by their own tests below.
_IDENTIFIER = re.compile(r"(?:mcp__vexa__)?[a-z][a-z0-9_]*")


def served() -> set:
    names = set(re.findall(r'operation_id="(\w+)"', BUILT_IN_SOURCE.read_text()))
    for pattern in MANIFEST_GLOBS:
        for path in REPO.glob(pattern):
            for tool in json.loads(path.read_text()).get("tools") or []:
                if set(tool.get("requires") or []) <= FULL_DEPLOYMENT:
                    names.add(tool["name"])
    return names


def allowlisted() -> set:
    return set(json.loads(ALLOWLIST.read_text())["tools"])


def vocabulary() -> set:
    """Every name that is a tool somewhere — the names a prompt could mean as a tool."""
    return served() | allowlisted() | RIG_ONLY


def references(text: str, vocab: set) -> set:
    text = text or ""
    found = set(_QUALIFIED.findall(text))
    for m in _REFERENCE.finditer(_QUALIFIED.sub(" ", text)):
        found.add(next(g for g in m.groups() if g))
    return found & vocab


def _strings(path: Path) -> list:
    """Every string constant in a module except its docstrings — the text a turn can be sent."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _skipped(path: Path) -> bool:
    return bool(_SKIP_PARTS & set(path.relative_to(REPO).parts)) or path.name.startswith("test_")


def prompt_sources():
    """``(where, text)`` for every prompt the product ships."""
    for tree in PROMPT_TREES:
        for path in sorted((REPO / tree).rglob("*")):
            if path.is_file() and path.suffix in {".md", ".txt", ".json", ".yaml", ".yml"}:
                yield str(path.relative_to(REPO)), path.read_text(encoding="utf-8")
    for pattern in PROMPT_TEXT_GLOBS:
        for path in sorted(REPO.glob(pattern)):
            yield str(path.relative_to(REPO)), path.read_text(encoding="utf-8")
    for tree in PROMPT_CODE_TREES:
        for path in sorted((REPO / tree).rglob("*.py")):
            if _skipped(path):
                continue
            for line, text in _strings(path):
                if not _IDENTIFIER.fullmatch(text):
                    yield f"{path.relative_to(REPO)}:{line}", text
    for rel in CLIENT_PROMPT_FILES:
        yield rel, (REPO / rel).read_text(encoding="utf-8")


# ── the extractor itself ─────────────────────────────────────────────────────────────────────────

def test_the_extractor_finds_tool_shaped_mentions_and_ignores_prose():
    vocab = {"bot_send", "propose", "meeting_transcript", "request_meeting_bot"}
    assert references("call `bot_send` now", vocab) == {"bot_send"}
    assert references("one `propose(claims=[...])` call", vocab) == {"propose"}
    assert references("Call the tool `mcp__vexa__meeting_transcript` with", vocab) == \
        {"meeting_transcript"}
    assert references("    bot_send(meeting_url=...)", vocab) == {"bot_send"}
    assert references("propose the first piece, then validate it", vocab) == set()
    assert references("ag.propose(uid, source=…)", vocab) == set()
    assert references("`request_meeting_bot` binds the meeting", vocab) == {"request_meeting_bot"}
    assert references("You did not read it. Call mcp__vexa__meeting_transcript with", vocab) == \
        {"meeting_transcript"}


def test_the_served_set_is_the_assembled_edge():
    names = served()
    assert {"get_meeting_transcript", "request_meeting_bot", "whats_waiting",
            "entity_upsert", "transcript_terms"} <= names
    assert not names & RIG_ONLY


# ── the guard ────────────────────────────────────────────────────────────────────────────────────

def test_every_tool_a_product_prompt_names_is_served():
    ok, vocab = served(), vocabulary()
    unserved = {}
    for where, text in prompt_sources():
        for name in references(text, vocab) - ok:
            unserved.setdefault(name, []).append(where)
    assert not unserved, (
        "product prompts name tools a standard deployment does not serve — serve each from its "
        "owning domain's manifest, or name the product tool that does the job:\n"
        + "\n".join(f"  {n}: {', '.join(sorted(set(w))[:6])}" for n, w in sorted(unserved.items())))


def test_the_imperative_gate_orders_only_served_verbs():
    from worker.engine import _IMPERATIVE_PATTERNS, imperative_preamble

    ordered = {tool for _pattern, tool, _phrase in _IMPERATIVE_PATTERNS}
    assert ordered <= served(), sorted(ordered - served())
    said = imperative_preamble("send the bot, join the meeting, stop the bot, stop recording, "
                               "schedule the bot")
    assert references(said, vocabulary()) <= served()


def test_the_refusal_detector_names_only_served_verbs():
    from worker.friction import _VERB_TOOL

    named = {tool for _verbs, tool in _VERB_TOOL}
    assert named <= served(), sorted(named - served())


@pytest.mark.parametrize("vocab_name", ["_BOT_TOOLS", "_TERMS_TOOLS", "_OPEN_TOOLS", "_FOCUS_TOOLS",
                                        "_WRITER_TOOLS"])
def test_the_harness_acts_only_on_results_of_served_tools(vocab_name):
    from llm import claude_code

    names = {t.removeprefix("mcp__vexa__") for t in getattr(claude_code, vocab_name)
             if t.startswith("mcp__vexa__")}
    assert names <= served() | ALLOWLIST_GAP, sorted(names - served() - ALLOWLIST_GAP)


# ── the worker's allow-list ──────────────────────────────────────────────────────────────────────

def test_the_allowlist_names_served_tools_and_exactly_the_tracked_gap():
    missing = allowlisted() - served()
    assert missing == ALLOWLIST_GAP, (
        f"newly served (drop from ALLOWLIST_GAP): {sorted(ALLOWLIST_GAP - missing)}; "
        f"allow-listed and not served: {sorted(missing - ALLOWLIST_GAP)}")


def test_every_agent_tool_the_edge_serves_is_in_the_allowlist():
    agent = json.loads((REPO / "core/agent/mcp.tools.v1.json").read_text())
    names = {t["name"] for t in agent["tools"]}
    assert names <= allowlisted(), sorted(names - allowlisted())
