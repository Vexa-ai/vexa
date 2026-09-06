"""The edge assembles its route table from the domains it fronts, and owns none of it.

PRD decision 40.5 — *"the gateway still owns nothing: it composes, strips authority, re-stamps,
forwards"* — and 40.7, which makes that concrete: **agents are optional**, so a table that names
`/agent/*` unconditionally cannot describe a `no-agents` deployment (40.6: gateway + meetings +
flows + identity).

What these tests hold down, in the order they matter:

  1. the FULL profile is byte-for-byte what shipped — 70 scoped rows and 2 unscoped, and every one
     of them still matched to a route that exists;
  2. without the agent domain the table lacks EXACTLY its seven rows and nothing else;
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

#: The agent domain's whole share of the edge — the seven rows that vanish in `no-agents`.
AGENT_ROWS = frozenset({
    ("POST", "/agent/chat"),
    ("GET", "/agent/meeting/stream"),
    ("GET", "/agent/{path:path}"),
    ("POST", "/agent/{path:path}"),
    ("PUT", "/agent/{path:path}"),
    ("PATCH", "/agent/{path:path}"),
    ("DELETE", "/agent/{path:path}"),
})
#: What shipped. A count, not a copy of the table: a second copy of 70 rows is a second thing to
#: keep in step, and `test_the_assembled_table_matches_the_app_exactly` is what proves the
#: CONTENT — against the routes themselves, which is a stronger anchor than a literal.
FULL_SCOPED, FULL_UNSCOPED = 70, 2
#: What THIS BUILD publishes — the full profile, less the agent rows when the build omits them.
#: DERIVED, so the count stays exact in either build rather than softening to a range or a
#: subset check. 70 on the line; 63 in a build with no agent manifest.
CARRIED_SCOPED = FULL_SCOPED - (0 if AGENT_CARRIED else len(AGENT_ROWS))


def _app(**kw):
    return create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis(), **kw)


# ── 1 · the full profile is unchanged ────────────────────────────────────────────────────────────

def test_the_published_table_is_exactly_what_this_build_serves():
    """An exact count either way: five domains publish 70 scoped rows, four publish 63. The
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
    # 7+2+12+12+39 = 72 rows, less the edge's own 2 unscoped = 70 = FULL_SCOPED above. meetings is
    # 39 because 0.12.27 ships the UNION of the three disputed edge routes: transcript-import and
    # the by-id share (the line's), and annotate-by-row-id (the candidate's). Both sides were
    # internally consistent before the merge — 38/69 and 37/68 — and neither literal survives it.
    assert a.domains == {"agent": 7, "gateway": 2, "identity": 12, "mcp": 12, "meetings": 39}
    assert {k for k, d in a.owner_of.items() if d == "agent"} == AGENT_ROWS
    # The EDGE declares two routes and they are its own — /health and /auth/me forward nothing.
    assert {k for k, d in a.owner_of.items() if d == "gateway"} == set(UNSCOPED_ROUTES)


# ── 2 · the no-agents profile lacks exactly the agent's rows ─────────────────────────────────────

@needs_agent
def test_without_the_agent_domain_the_table_lacks_exactly_its_seven_rows():
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
    of the four domains, so it asserts 63 rows on the line and in the open-core build alike."""
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
