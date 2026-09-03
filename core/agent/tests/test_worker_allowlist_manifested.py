"""THE WORKER'S ALLOW-LIST vs. WHAT THE ASSEMBLED EDGE ACTUALLY SERVES.

ADR-0037 / PRD decision 40.5: the worker's `mcp_tools.v1.json` (`core/agent/worker/`) names the
tools it may CALL through the one assembled MCP edge; a domain's `mcp.tools.v1.json` names the
tools that domain PUBLISHES into that edge. The allow-list is only meaningful once every name in it
is actually served — an allowed tool the edge does not serve is not a narrower permission, it is a
silent 404 waiting for a worker turn to hit it.

BOTH SIDES READ FROM SOURCE, never restated here: the allow-list is the shipped
`core/agent/worker/mcp_tools.v1.json`; the served surface is the union of every domain's own
`core/*/mcp.tools.v1.json` (the same glob `test_manifest_files.py` in the mcp package's own suite
uses to find them) plus MCP's fourteen built-in tools, which this test does not need to name because
none of them collide with the allow-list's own 24.

TODAY THE UNION IS SMALLER THAN THE ALLOW-LIST, on purpose and by name — GAP below is that
migration backlog, exactly as `scripts/domain-doors.allow.json` tracks the doors still open. `bind.py`
now derives an argument's schema from a route's OpenAPI `requestBody` as well as its `parameters`
(the assembler issue this manifest and `core/flows/mcp.tools.v1.json` shipped beside), which moved
`flow_lifecycle`, `flows_submit` and `workspace_new` OUT of this backlog — three kinds of entry
remain, and the note beside each says which:

  * meetings tools with a real backing route this manifest never touches (`meetings_list`,
    `bot_send`, `bot_stop`, `meeting_transcript`, `transcript_terms`, `meeting_info` are meeting-api
    routes reached through the gateway) — another domain's manifest to write, not this one's;
  * agent-api routes that exist but are STILL not bindable, for a narrower reason than before —
    `workspace_write`, `entity_upsert`, `propose` each take a bare `body: dict = Body(...)`, which
    FastAPI publishes with NO named `properties` (`{"type": "object", "additionalProperties": true}`)
    — there is nothing there for `bind.py` to derive a schema from, unlike `workspace_new`'s
    `WorkspaceNewBody` or flows' `FlowSubmission`, which are named pydantic models. Closing this one
    needs those three routes moved to named body models in
    `core/agent/control_plane/routers/workspaces.py` + `api_shared.py` first (see
    `core/agent/mcp.tools.v1.json`'s own top-level `note`);
  * genuinely no server home yet — `validate`, `mark_scaffolded`, `company_context` are explicitly
    scoped OUT of `control_plane/claims.py` ("remain the rig's for now"); `vexa_overview` reads a
    public docs URL directly; `start_onboarding` is the rig's own onboarding/mail flow.

CHECKED IN BOTH DIRECTIONS, same reason domain-doors.allow.json is: GAP shrinking without this test
changing is a name this test forgot to stop tracking, and GAP growing without this test changing is
a newly-allow-listed tool nobody manifested. Either one should fail here rather than be discovered
by a worker calling a tool that is not there.
"""
from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]
ALLOWLIST_PATH = REPO / "core" / "agent" / "worker" / "mcp_tools.v1.json"
MANIFEST_GLOBS = ["core/*/mcp.tools.v1.json", "core/*/services/*/mcp.tools.v1.json"]

#: The exact migration backlog, as of the manifest this test shipped beside — see the module
#: docstring for why each name is here rather than in a manifest.
GAP = {
    "meeting_transcript", "meetings_list", "bot_send", "bot_stop", "meeting_info",   # meetings domain
    "transcript_terms",                                                             # meetings domain
    "workspace_write", "entity_upsert", "propose",                                  # agent: untyped dict body
    "validate", "mark_scaffolded", "company_context",                               # agent: no server home
    "vexa_overview", "start_onboarding",                                            # agent: no server home
}


def _allowlisted() -> set:
    return set(json.loads(ALLOWLIST_PATH.read_text())["tools"])


def _manifested() -> set:
    names = set()
    for pattern in MANIFEST_GLOBS:
        for p in REPO.glob(pattern):
            names |= {t["name"] for t in json.loads(p.read_text()).get("tools") or []}
    return names


def test_the_allowlist_and_the_manifests_actually_disagree_by_exactly_the_named_gap():
    allowlisted, manifested = _allowlisted(), _manifested()
    missing = allowlisted - manifested
    assert missing == GAP, (
        f"the worker allow-list and the published manifests moved:\n"
        f"  newly covered (drop from GAP): {sorted(GAP - missing)}\n"
        f"  newly uncovered (add to GAP):  {sorted(missing - GAP)}\n"
        "Update GAP (and, for a name that is now covered, celebrate — that is the point of this "
        "test getting smaller) rather than deleting the assertion.")


def test_the_gap_names_only_tools_the_allowlist_actually_carries():
    """GAP is a subset of the allow-list by construction — a stale entry here would silently stop
    covering anything the moment the allow-list itself changed shape."""
    assert GAP <= _allowlisted()


def test_every_agent_bound_tool_the_manifest_declares_is_in_the_worker_allowlist():
    """The inverse direction: nothing agent-api's own manifest serves should be a tool the worker
    is refused. `core/agent/mcp.tools.v1.json` is the file this reads; a name only there and not in
    the allow-list is a tool a worker cannot reach even though the edge would serve it."""
    agent_manifest = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.json").read_text())
    agent_tool_names = {t["name"] for t in agent_manifest["tools"]}
    assert agent_tool_names <= _allowlisted(), (
        f"agent-api's manifest serves tools the worker's allow-list does not name: "
        f"{sorted(agent_tool_names - _allowlisted())}")
