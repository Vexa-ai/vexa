"""The edge assembles its route table from the domains it fronts, and owns none of it.

PRD decision 40.5 — *"the gateway still owns nothing: it composes, strips authority, re-stamps,
forwards"* — and 40.7, which makes that concrete: **agents are optional**, so a table that names
`/agent/*` unconditionally cannot describe a `no-agents` deployment (40.6: gateway + meetings +
flows + identity).

What these tests hold down, in the order they matter:

  1. the FULL profile is byte-for-byte what shipped — 150 scoped rows and 2 unscoped, and every one
     of them still matched to a route that exists;
  2. without the agent domain the table lacks EXACTLY its fifty rows and nothing else;
  3. an absent domain's routes are absent from the APP too, so a request 404s rather than 403s;
  4. the refusals that make the assembly safe to compose from files.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from gateway import ROUTE_SCOPES, UNSCOPED_ROUTES, create_app, routes_manifest, undeclared_routes
from gateway.routes_manifest import ManifestError

from conftest import AGENT_CARRIED, VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, needs_agent

#: The rows the agent's MCP tools call back into (`"mcp_reentry": true`), one per route a tool in
#: `core/agent/mcp.tools.v1.json` calls — held to that file by `core/agent/tests/test_edge_forward.py`.
AGENT_CALLBACK_ROWS = frozenset({
    ("POST", "/agent/agents/message"),
    ("GET", "/agent/chats/search"),
    ("GET", "/agent/chats/{session}/turns"),
    ("GET", "/agent/chat/folders"),
    ("POST", "/agent/chat/folders"),
    ("POST", "/agent/chat/folders/delete"),
    ("POST", "/agent/chat/folders/update"),
    ("POST", "/agent/chat/move"),
    ("GET", "/agent/notifications"),
    ("POST", "/agent/notifications/read"),
    ("POST", "/agent/notifications/notify"),
    ("POST", "/agent/notifications/act"),
    ("POST", "/agent/workspace/invite-link"),
    ("GET", "/agent/workspace/invite-links"),
    ("POST", "/agent/workspace/invite-link/revoke"),
    ("POST", "/agent/workspace/policy/proposals"),
    ("GET", "/agent/workspace-store/fetch"),
    ("POST", "/agent/workspace-store/push"),
    ("GET", "/agent/refs/resolve"),
    ("GET", "/agent/home/history"),
    ("GET", "/agent/home/customer"),
    ("POST", "/agent/home/customer/correct"),
    ("POST", "/agent/home/item"),
    ("GET", "/agent/budgets"),
    ("GET", "/agent/budgets/usage"),
    ("GET", "/agent/usage"),
    ("GET", "/agent/models/levels"),
    ("GET", "/agent/people/followups"),
    ("GET", "/agent/people/discover"),
    ("POST", "/agent/chat/name/agent"),
    ("GET", "/agent/claims"),
    ("POST", "/agent/claims"),
    ("POST", "/agent/claims/verdicts"),
    ("GET", "/agent/connections"),
    ("POST", "/agent/connections/calendar/event/cancel"),
    ("POST", "/agent/connections/calendar/event/create"),
    ("POST", "/agent/connections/calendar/event/update"),
    ("POST", "/agent/connections/calendar/events"),
    ("POST", "/agent/connections/gmail/draft"),
    ("POST", "/agent/connections/gmail/draft/update"),
    ("POST", "/agent/connections/gmail/inbox"),
    ("POST", "/agent/connections/gmail/read"),
    ("POST", "/agent/connections/gmail/search"),
    ("POST", "/agent/connections/gmail/thread"),
    ("GET", "/agent/connections/policy"),
    ("PUT", "/agent/connections/policy"),
    ("POST", "/agent/connections/request"),
    ("POST", "/agent/connections/sensor-grant"),
    ("GET", "/agent/connections/sensor-grants"),
    ("POST", "/agent/connections/sensor-grant/revoke"),
    ("POST", "/agent/connections/service/call"),
    ("POST", "/agent/global/ready"),
    ("POST", "/agent/meeting/terms/scan"),
    ("POST", "/agent/meeting/note/attach"),
    ("GET", "/agent/models/catalog"),
    ("POST", "/agent/onboarding/research"),
    ("POST", "/agent/page/open"),
    ("POST", "/agent/meeting/listen"),
    ("GET", "/agent/time"),
    ("PUT", "/agent/time/zone"),
    ("GET", "/agent/sessions/{session}/participants"),
    ("POST", "/agent/sessions/{session}/participants"),
    ("GET", "/agent/rooms/list"),
    ("POST", "/agent/rooms/call"),
    ("DELETE", "/agent/sessions/{session}/participants/{principal}"),
    ("POST", "/agent/sessions/{session}/call/show"),
    ("POST", "/agent/sessions/{session}/call/highlight"),
    ("POST", "/agent/workspace/asset"),
    ("GET", "/agent/workspaces/{slug}/identity/state"),
    ("PUT", "/agent/workspaces/{slug}/identity/icon"),
    ("POST", "/agent/workspace/entity"),
    ("GET", "/agent/workspace/file"),
    ("PUT", "/agent/workspace/file"),
    ("POST", "/agent/workspace/import"),
    ("GET", "/agent/workspace/import/{operation_id}/status"),
    ("GET", "/agent/workspace/folder/{plan_id}/status"),
    ("POST", "/agent/workspace/invite"),
    ("GET", "/agent/workspace/members"),
    ("POST", "/agent/workspace/members/add"),
    ("POST", "/agent/workspace/membership"),
    ("POST", "/agent/workspace/move"),
    ("POST", "/agent/workspace/new"),
    ("POST", "/agent/workspace/rename"),
    ("POST", "/agent/workspace/forget"),
    ("DELETE", "/agent/workspace/{slug}"),
    ("POST", "/agent/workspace/pull"),
    ("GET", "/agent/workspace/purpose"),
    ("POST", "/agent/workspace/push"),
    ("POST", "/agent/workspace/publish"),
    ("GET", "/agent/workspace/git-providers"),
    ("POST", "/agent/workspace/remove"),
    ("GET", "/agent/workspace/shared"),
    ("POST", "/agent/workspace/shared/{workspace_id}/attach"),
    ("POST", "/agent/workspace/status"),
    ("POST", "/agent/workspace/target"),
    ("POST", "/agent/workspace/propose-target"),   # UI decision 6b — the agent asks, the person switches
    ("GET", "/agent/workspace/tree"),
    ("GET", "/agent/workspace/states"),
    ("POST", "/agent/workspace/state"),
    ("POST", "/agent/workspace/defaults"),
    ("GET", "/agent/workspace/profiles"),
    ("POST", "/agent/workspace/profile"),
    ("POST", "/agent/workspace/profiles/edit"),
    ("POST", "/agent/workspace/profiles/delete"),
    ("GET", "/agent/workspace/templates"),
    ("POST", "/agent/workspace/create"),
    ("POST", "/agent/workspace/templates/save"),
})
#: The agent domain's whole share of the edge — the rows that vanish in `no-agents`: its own eight
#: and the MCP callback rows above.
AGENT_ROWS = frozenset({
    ("POST", "/agent/chat"),
    ("GET", "/agent/meeting/stream"),
    ("POST", "/agent/friction"),
    ("GET", "/agent/{path:path}"),
    ("POST", "/agent/{path:path}"),
    ("PUT", "/agent/{path:path}"),
    ("PATCH", "/agent/{path:path}"),
    ("DELETE", "/agent/{path:path}"),
}) | AGENT_CALLBACK_ROWS
#: What shipped. A count, not a copy of the table: a second copy of 188 rows is a second thing to
#: keep in step, and `test_the_assembled_table_matches_the_app_exactly` is what proves the
#: CONTENT — against the routes themselves, which is a stronger anchor than a literal.
FULL_SCOPED, FULL_UNSCOPED = 214, 3   # + GET /transcription/languages, + POST /agent/connections/sensor-grant, GET …/sensor-grants, POST …/sensor-grant/revoke (08S-b), + POST /bots/{platform}/{native_meeting_id}/chat (#1089), + GET …/chat/{request_id} (the send's delivery status), + GET/POST /agent/workspace-store/* (WS-S3), + /internal/mcp/taint (D6), + POST /agent/workspace/policy/proposals (int-s1), + GET/PUT /user/notifications (S6), + POST /agent/rooms/call (call-person-1018), + GET /agent/rooms/list (held-label-rooms-1018)
#: What THIS BUILD publishes — the full profile, less the agent rows when the build omits them.
#: DERIVED, so the count stays exact in either build rather than softening to a range or a
#: subset check.
CARRIED_SCOPED = FULL_SCOPED - (0 if AGENT_CARRIED else len(AGENT_ROWS))


def _app(**kw):
    return create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis(), **kw)


# ── 1 · the full profile is unchanged ────────────────────────────────────────────────────────────

def test_the_published_table_is_exactly_what_this_build_serves():
    """An exact count either way: five domains publish 180 scoped rows, four publish 83. The
    expectation is derived from what the build carries, never relaxed to accommodate it."""
    assert (len(ROUTE_SCOPES), len(UNSCOPED_ROUTES)) == (CARRIED_SCOPED, FULL_UNSCOPED)


@needs_agent
def test_the_full_profile_carries_the_agent_rows():
    """The other half of the count, and it is a separate test on purpose: in a build with no agent
    manifest this is not true, not vacuously true, and not quietly dropped — it is SKIPPED, and
    pytest prints why."""
    assert (len(ROUTE_SCOPES), len(UNSCOPED_ROUTES)) == (FULL_SCOPED, FULL_UNSCOPED)
    assert AGENT_ROWS <= set(ROUTE_SCOPES)


def test_the_assembled_table_matches_the_app_exactly():
    """Every declared row is a route, and every route is declared. This is the anchor: it ties the
    manifests to the thing they describe, so a row that drifts is caught by the routes themselves
    rather than by a copy of the table living in a test."""
    app = _app()
    registered = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
                  for m in (r.methods or ()) if m != "HEAD"}
    declared = set(ROUTE_SCOPES) | set(UNSCOPED_ROUTES)
    assert registered == declared, {
        "declared but not registered": sorted(declared - registered),
        "registered but not declared": sorted(registered - declared)}


@needs_agent
def test_every_domain_declares_its_own_and_only_its_own():
    a = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent"})
    # 105+2+19+12+56 = 194 rows, less the edges own 2 unscoped = 192 = FULL_SCOPED above. The agents
    # 105 are its own eight plus one row per route its MCP tools call back into. meetings is
    # 39 because 0.12.27 ships the UNION of the three disputed edge routes: transcript-import and
    # the by-id share (the line's), and annotate-by-row-id (the candidate's). Both sides were
    # internally consistent before the merge — 38/69 and 37/68 — and neither literal survives it.
    # +4: the owner's side of a meeting share (GET/PATCH …/access, DELETE …/share/{grant_id},
    # DELETE …/viewers/{viewer_id}).
    # The agent's 55th is workspace_status, the look-only half of a sync (Vexa-ai/vexa#1783); 56-60
    # are the chat-folder tools (list, create, update, delete, move — Vexa-ai/vexa#1783); 61-64 are
    # workspace_member_add and the three chat-participant verbs (ADR-0048); 65-66 are the
    # notification tools (list, mark read — Vexa-ai/vexa#1783); 67 is workspace_folder_import_status (a dropped folder becomes a workspace); 68-69 are
    # workspace_rename and workspace_remove (Vexa-ai/vexa#1783); 70 is notify (an agent tells a person);
    # 74-76 are the workspace-state verbs (workspace_list, workspace_state_set, workspace_defaults_set);
    # 77-79 the chat-profile verbs (chat_profile_list, chat_profile_set, chat_profile_edit);
    # 80 is notifications_act, a notification acted on (Vexa-ai/vexa#1783).
    # 81-83 are the workspace join-link verbs (workspace_invite_link_create, _links, _revoke).
    # 84-86 are the workspace-template verbs (workspace_templates_list, workspace_create, workspace_template_save).
    # 87-88 are workspace_publish and git_providers (the git provider port, Vexa-ai/vexa#1783).
    # 89 is ref_resolve, a universal reference resolved for the person (Vexa-ai/vexa#1783).
    # 90-97 are the home's (home_history, customer_profile, customer_correct, home_item_update),
    # the budgets' (my_limits, usage_summary) and the people's (people_followups, people_discover).
    # 98 is chat_profile_delete.
    # 99-100 are call_show and call_highlight (a page shown to everyone in a call, Vexa-ai/vexa#1783).
    # 101 is meeting_listen, the listening brief of a live meeting (transcript signals).
    # meetings' 48th is POST /voice-notes/recorder, the record_voice_note tool (Vexa-ai/vexa#1783).
    # The seven bot-account rows (ADR-0049) then took meetings from 48 to 55.
    # 101 is meeting_attach_page (a page made the meeting's page, #1783).
    # identity's 17-19 are the person's own photo (GET/PUT/DELETE /user/photo, UI 15g).
    # meetings 56 is a room's planned call as an iCalendar file (GET /meetings/{meeting_id}/ics, rooms F1).
    # 106-107 are plan_usage (the usage meter, feature `usage`) and model_levels (levels 1-5, feature `levels`), #1783.
    # identity 20-26: the public profile, company verification and the directory (`public_profiles`, R4).
    # 111 includes call_person, "Call <person>": a Vexa call in the room of two (call-person-1018).
    # 112 is rooms_list, a workspace's rooms described for the agent (held-label-rooms-1018).
    # 113-115 are calendar_sensor_grant, calendar_sensor_grants and calendar_sensor_grant_revoke (08S-b).
    # meetings 57 is POST /bots/{platform}/{native_meeting_id}/chat, the in-meeting chat send (#1089).
    # meetings 58 is GET /bots/{platform}/{native_meeting_id}/chat/{request_id}, whether that send posted.
    assert a.domains == {"agent": 115, "gateway": 3, "identity": 28, "mcp": 12, "meetings": 59}
    assert {k for k, d in a.owner_of.items() if d == "agent"} == AGENT_ROWS
    # The EDGE declares two routes and they are its own — /health and /auth/me forward nothing.
    assert {k for k, d in a.owner_of.items() if d == "gateway"} == set(UNSCOPED_ROUTES)


# ── 2 · the no-agents profile lacks exactly the agent's rows ─────────────────────────────────────

@needs_agent
def test_without_the_agent_domain_the_table_lacks_exactly_its_own_rows():
    full = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent"})
    lean = routes_manifest.load({"gateway", "meetings", "identity", "mcp"})
    assert set(full.scopes) - set(lean.scopes) == AGENT_ROWS
    assert set(lean.scopes) - set(full.scopes) == set()
    assert lean.unscoped == full.unscoped


def test_the_no_agents_app_does_not_register_the_agent_routes():
    app = _app(agent_api_url="")
    registered = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
                  for m in (r.methods or ()) if m != "HEAD"}
    assert registered & AGENT_ROWS == set()
    assert ("GET", "/bots") in registered, "only the agent domain went away"
    assert undeclared_routes(app, routes_manifest.load(
        {"gateway", "meetings", "identity", "mcp"}).scopes,
        routes_manifest.load({"gateway", "meetings", "identity", "mcp"}).unscoped) == []


# ── 3 · absent is 404, not 401 and not 403 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("post", "/agent/chat"), ("get", "/agent/meeting/stream"), ("get", "/agent/sessions"),
])
def test_a_request_to_an_absent_domain_is_404_not_401(method, path):
    """404 is the only honest answer: this deployment does not serve it. 401 would say "sign in and
    it will work" and 403 would say "you may not" — both describe a route that exists, and a client
    told either one retries, escalates, or asks for a scope nobody can grant."""
    client = TestClient(_app(agent_api_url=""))
    for headers in ({}, {"x-api-key": VALID_KEY}):
        r = getattr(client, method)(path, headers=headers)
        assert r.status_code == 404, (path, headers, r.status_code)


@needs_agent
def test_the_same_request_is_served_when_the_agent_domain_is_there():
    """The half a blanket 404 would break."""
    client = TestClient(_app())
    assert client.get("/agent/sessions", headers={"x-api-key": VALID_KEY}).status_code != 404


# ── 4 · the refusals that make composing from files safe ─────────────────────────────────────────

def _doc(domain, rows):
    return {"contract": "routes.v1", "domain": domain, "routes": rows}


def test_two_domains_claiming_one_route_refuse_to_boot():
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([
            _doc("meetings", [{"method": "GET", "path": "/x", "scopes": ["bot"]}]),
            _doc("agent", [{"method": "GET", "path": "/x", "scopes": ["tx"]}]),
        ])
    assert "meetings" in str(e.value) and "agent" in str(e.value), \
        "the refusal must name BOTH files, or the operator has two to find"


def test_a_scope_outside_the_vocabulary_refuses_to_boot():
    """An unknown scope is a set no key can hold — a deny that reads like a decision somebody made."""
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": "/x",
                                                  "scopes": ["bot", "amin"]}])])
    assert "amin" in str(e.value)


@pytest.mark.parametrize("row", [
    {"method": "FETCH", "path": "/x", "scopes": ["bot"]},
    {"method": "GET", "path": "agent/x", "scopes": ["bot"]},
])
def test_a_row_that_is_not_a_route_refuses_to_boot(row):
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("agent", [row])])


def test_a_manifest_of_the_wrong_contract_refuses_to_boot(tmp_path):
    p = tmp_path / "routes.v1.json"
    p.write_text('{"contract": "mcp.tools.v1", "domain": "agent", "routes": []}')
    with pytest.raises(ManifestError) as e:
        routes_manifest.read(p)
    assert "routes.v1" in str(e.value)


def test_a_deployed_domain_with_no_manifest_refuses_to_boot():
    with pytest.raises(ManifestError):
        routes_manifest.load({"gateway", "billing"})


# ── 5 · a domain this CUT does not ship ──────────────────────────────────────────────────────────
#
# Absence has TWO layers and they are different facts.
#
#   a DEPLOYMENT without agents  — `AGENT_API_URL` unset; the manifest is on disk, unused (§3)
#   a CUT without agents         — the open-core cut is generated by dropping the agent surface,
#                                  so `core/agent/routes.v1.json` is not on disk AT ALL
#
# Module scope asserted all five manifests were present, so in that cut `import gateway` raised
# ManifestError and took EVERY test module in this package down at COLLECTION — eleven of them,
# before one assertion ran — while this module's own docstring says an absent domain contributes
# no manifest and that is not an error.
#
# THE RELAXATION IS EXACTLY ONE LAYER WIDE, and the last three tests here are what hold that down.
# Module scope publishes CANDIDATES: "what would a complete deployment of this cut serve". A
# running app's table is a PROMISE: `create_app` still assembles with the strict `load` over the
# domains the deployment NAMED, and a named domain it cannot describe still refuses to boot.

#: The domains the open-core cut carries. `agent` is deliberately not among them.
_OSS_CUT = ("gateway", "meetings", "identity", "mcp")

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
#: Resolved ONCE, at import. Two tests below monkeypatch `_repo_root` to point the module at a cut,
#: and `_cut` calling it would then build the cut out of itself.
_REAL_ROOT = routes_manifest._repo_root()


def _cut(tmp_path, domains):
    """A repo root carrying only `domains`' manifests, copied from the real ones."""
    root = _REAL_ROOT
    real = routes_manifest.manifest_paths(root)
    for d in domains:
        dst = tmp_path / real[d].relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(real[d].read_text())
    return tmp_path


def test_a_domain_this_cut_does_not_ship_is_simply_absent(tmp_path):
    """The bug at the assembly layer: this raised, so the package could not be imported at all.

    Independent of which build is running it — the cut is generated here from the real manifests
    of the four domains, so it asserts 83 rows on the line and in the open-core build alike."""
    a = routes_manifest.load_carried(_OSS_CUT + ("agent",), repo_root=_cut(tmp_path, _OSS_CUT))
    assert (len(a.scopes), len(a.unscoped)) == (FULL_SCOPED - len(AGENT_ROWS), FULL_UNSCOPED)
    assert not [k for k in a.scopes if k[1].startswith("/agent")]
    assert set(a.domains) == set(_OSS_CUT)


@needs_agent
def test_the_full_checkout_still_assembles_every_row(tmp_path):
    """Tolerating absence must not lose a manifest that IS there — the control for the test above."""
    a = routes_manifest.load_carried(_OSS_CUT + ("agent",),
                                     repo_root=_cut(tmp_path, _OSS_CUT + ("agent",)))
    assert (len(a.scopes), len(a.unscoped)) == (FULL_SCOPED, FULL_UNSCOPED)
    assert AGENT_ROWS <= set(a.scopes)


def test_a_domain_name_this_module_never_heard_of_is_a_typo_not_a_cut(tmp_path):
    """A MISSING FILE is a cut; a name with no entry in `manifest_paths` is a misspelling, and
    tolerating it would let one silently shrink the published table with nothing left to read."""
    with pytest.raises(ManifestError) as e:
        routes_manifest.load_carried(("gateway", "agnet"), repo_root=_cut(tmp_path, ("gateway",)))
    assert "agnet" in str(e.value)


def test_importing_the_package_in_a_cut_with_no_agent_manifest_raises_nothing(tmp_path):
    """The bug as the open-core cut meets it: a real `import gateway` from a tree with no agent
    manifest. The tests above pin the assembly; this one pins the IMPORT, which is where it
    actually failed. The package is copied into the cut so `_repo_root()` — which resolves
    `__file__` and walks up — lands on the cut's root and not on this checkout's."""
    root = _cut(tmp_path, _OSS_CUT)
    src = root / "core/gateway/services/gateway/src"
    shutil.copytree(_SRC, src, dirs_exist_ok=True)
    assert not (root / "core/agent/routes.v1.json").exists()

    r = subprocess.run(
        [sys.executable, "-c",
         "import gateway; print(len(gateway.ROUTE_SCOPES), len(gateway.UNSCOPED_ROUTES))"],
        cwd=str(root), env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True)
    assert r.returncode == 0, f"import gateway failed in a no-agent cut:\n{r.stderr}"
    assert r.stdout.split() == [str(FULL_SCOPED - len(AGENT_ROWS)), str(FULL_UNSCOPED)]


def test_a_deployed_domain_with_no_manifest_still_refuses(tmp_path):
    """The strict `load` is untouched: a domain NAMED with no manifest behind it is still a fault."""
    with pytest.raises(ManifestError):
        routes_manifest.load({"gateway", "agent"}, repo_root=_cut(tmp_path, ("gateway",)))


def test_an_app_that_names_a_domain_it_cannot_describe_still_refuses_to_build(tmp_path, monkeypatch):
    """The promise layer, through the front door. `create_app` — not `load` in isolation — must
    still refuse when the deployment sets `AGENT_API_URL` in a tree with no agent manifest: the
    app would otherwise register `/agent/*` and serve routes no manifest scopes, which is the
    deny-by-default hole the whole module exists to close."""
    monkeypatch.setattr(routes_manifest, "_repo_root", lambda: _cut(tmp_path, _OSS_CUT))
    with pytest.raises(ManifestError):
        _app(agent_api_url="http://agent-api")


def test_the_same_app_builds_in_that_cut_when_it_names_no_agent(tmp_path, monkeypatch):
    """The control: the refusal above must be about the NAMING, not about the cut. Same tree, no
    agent named — the app builds, and answers /agent with the truth (404)."""
    monkeypatch.setattr(routes_manifest, "_repo_root", lambda: _cut(tmp_path, _OSS_CUT))
    client = TestClient(_app(agent_api_url=""))
    assert client.get("/health").status_code == 200
    assert client.get("/agent/sessions", headers={"x-api-key": VALID_KEY}).status_code == 404
