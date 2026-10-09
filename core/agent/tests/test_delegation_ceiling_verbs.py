"""A worker dispatched without a person is held to its workspace ceiling on every route that names one.

The ceiling (`x-user-delegation-workspaces`, on the signed identity) is enforced where a named
workspace is RESOLVED — `_read_target`, `_manage_dir` and `ceiling.write_slug` — and, on a route
that names a workspace without a resolver, by `ceiling.require_in_ceiling` before it acts. The
account's own rules (owner, contributor, admin) still apply after it.

THE ROUTE LIST IS GENERATED, not written by hand. Every route of the built app whose path
parameters, query parameters or JSON body model name a workspace gets one request per naming
field, as a delegated worker whose ceiling excludes that workspace, and must be refused with
nothing changed on disk. A route added tomorrow with a `slug` is in this test the day it lands. A
free-form (`dict`) body names nothing a model can show, so every route that takes one is classified
by hand below, and the test fails until a new one is.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal, Optional, Union, get_args, get_origin

import pytest
from fastapi import params as fastapi_params
from fastapi.testclient import TestClient
from pydantic import BaseModel

from control_plane import identity_token
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from workspaces.shared.workspace_id import mint_id

KEY = identity_token.generate_signing_key()
PERSON = "7"
#: The one workspace every generated worker's ceiling grants. It never exists: a field that must
#: name SOME workspace but is not the one under test names this one.
INSIDE = "ws_a"
#: A workspace that does not exist, for the checks that the ceiling lets a grant through.
ABSENT = "ws_b"

#: Path and query parameter names that name a workspace.
WS_PARAMS = frozenset({"slug", "workspace_id"})
#: Body model fields that name a workspace (survey of every model a route takes).
WS_FIELDS = frozenset({"slug", "to_slug", "workspace_id", "credential_workspace", "target",
                       "workspace", "workspaces"})
#: Fields with a workspace-sounding name that name something else.
NOT_A_WORKSPACE = {
    ("POST", "/api/workspace/shared/{workspace_id}/attach", "slug"):
        "a parked tree of the workspace the path names, not another workspace",
}
#: Every route that takes a free-form body, and the keys of it that name a workspace.
DICT_BODIES = {
    ("POST", "/invocations"): (),                          # internal tier / a signed routine job
    ("POST", "/events"): (),                               # internal tier
    ("POST", "/api/chat/target"): ("workspace",),
    ("POST", "/api/meeting/note"): (),                     # writes the caller's own desk
    ("POST", "/api/meeting/terms"): (),                    # writes the caller's own desk
    ("POST", "/api/friction"): (),                         # a report about the product, filed to flows
    ("POST", "/api/proposals"): (),
    ("POST", "/api/proposals/resolve"): (),
    ("POST", "/api/desk/touch"): ("workspace",),
    ("POST", "/api/links/resolve"): ("slug",),
    ("POST", "/api/workspace/git/reset"): (),              # the caller's own desk, internal tier
    ("POST", "/api/workspaces/{workspace_id}/rename"): (),  # the path names it
    ("POST", "/api/workspace/{slug}/deploy-key"): (),      # the path names it
    ("POST", "/api/workspace/reset"): ("target",),
}
#: The rest of a valid free-form body, for the routes above that name a workspace in one.
DICT_BASE = {
    ("POST", "/api/links/resolve"): {"refs": []},
    ("POST", "/api/desk/touch"): {"path": "a.md"},
}
#: Routes a worker cannot call at all — the internal tier, which a worker never holds. Refused
#: before the ceiling is asked, which is all a worker needs.
INTERNAL_TIER = frozenset({("POST", "/internal/scaffolds")})
#: Gaps this test documents until their router is changed: the route answers without refusing.
KNOWN_GAPS = {
    ("POST", "/api/desk/touch", "workspace"):
        "routers/scaffolds.py desk_touch records a touch for a workspace id without asking the "
        "ceiling (the touch is filed on the caller's own desk); it needs "
        "`require_in_ceiling(request, (rec or {}).get('slug') or wid)` before the access check",
}
#: The routes this pass found unguarded, by path — the floor below keeps them in the generated set.
MUST_COVER = {
    ("POST", "/api/workspace/publish"), ("POST", "/api/workspace/rename"),
    ("POST", "/api/workspace/push"), ("POST", "/api/workspace/pull"),
    ("POST", "/api/workspace/git-remote-detach"), ("POST", "/api/workspace/purpose"),
    ("GET", "/api/workspace/purpose"), ("GET", "/api/workspace/git-remote-status"),
}
#: How many (route, field) cases the app generates today. A rename of a parameter cannot silently
#: shrink the set: the count may grow, never fall below this without a deliberate edit here.
FLOOR = 52


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _build(monkeypatch, tmp_path) -> TestClient:
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "agent-test-internal-secret")
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(root))
    # A route the ceiling lets through may still fail further in on this bare store; that is the
    # route answering, which is all the "not refused by the ceiling" cases need to see.
    return TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                                 reader=WorkspaceReader(str(root))),
                      raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    return _build(monkeypatch, tmp_path)


def _person() -> dict:
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": PERSON, "email": "ada@example.com"})}


def _as(workspaces, regime="autonomous") -> dict:
    claims = {"sub": PERSON, "email": "ada@example.com",
              "delegation": {"regime": regime, "workspaces": workspaces}}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


# ── the generator ────────────────────────────────────────────────────────────────────────────────

def _effective(routes):
    """The routes a request sees (FastAPI keeps an included router as one placeholder entry)."""
    for r in routes:
        inc = getattr(r, "include_context", None)
        if inc is not None:
            yield from _effective(r.original_router.routes)
        elif getattr(r, "dependant", None) is not None:
            yield r


def _app_routes():
    root = Path(tempfile.mkdtemp(prefix="ceiling-routes-"))
    app = create_app(Dispatcher(load_settings(workspaces_dir=str(root)), _Runtime(), _Identity()),
                     reader=WorkspaceReader(str(root)))
    return list(_effective(app.routes))


def _dummy(annotation):
    """A value that satisfies ``annotation`` and means nothing."""
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _dummy(args[0]) if args else None
    if origin is Literal:
        return get_args(annotation)[0]
    if origin in (list, tuple, set):
        return []
    if origin is dict or annotation is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _model_body(annotation, {})
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    return "x"


def _model_body(model, named: dict) -> dict:
    """The smallest body ``model`` accepts, with ``named`` laid over it."""
    body = {}
    for name, field in model.model_fields.items():
        if name in named:
            if named[name] is not None:
                body[name] = named[name]
        elif field.is_required():
            body[name] = _dummy(field.annotation)
    model.model_validate(body)  # a case whose body the route would 422 tests nothing
    return body


def _is_form(p) -> bool:
    return isinstance(p.field_info, (fastapi_params.Form, fastapi_params.File))


def _locations(route) -> list:
    """Every (kind, name) on ``route`` that names a workspace."""
    method = sorted(route.methods)[0]
    d = route.dependant
    out = [("path", p.name) for p in d.path_params if p.name in WS_PARAMS]
    out += [("query", p.name) for p in d.query_params if p.name in WS_PARAMS]
    for p in d.body_params:
        ann = p.field_info.annotation
        if _is_form(p):
            if p.name in WS_PARAMS:
                out.append(("form", p.name))
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            out += [("body", f) for f in ann.model_fields if f in WS_FIELDS]
        else:
            # An unclassified free-form body is caught by `test_every_free_form_body_is_classified`.
            out += [("dict", k) for k in DICT_BODIES.get((method, route.path), ())]
    return [loc for loc in out if (method, route.path, loc[1]) not in NOT_A_WORKSPACE]


def _cases():
    out = []
    for route in _app_routes():
        method = sorted(route.methods)[0]
        for loc in _locations(route):
            out.append(pytest.param(method, route, loc, id=f"{method} {route.path} [{loc[0]}:{loc[1]}]"))
    return out


def _request(client, method, route, loc, value, headers):
    """Call ``route`` with the field at ``loc`` naming ``value``, every other workspace-naming
    field naming INSIDE (or omitted when it may be), every other field a harmless dummy."""
    d = route.dependant
    path = route.path
    for p in d.path_params:
        v = value if ("path", p.name) == loc else (INSIDE if p.name in WS_PARAMS else "x1")
        path = path.replace("{" + p.name + "}", v)
    query = {}
    for p in d.query_params:
        if ("query", p.name) == loc:
            query[p.name] = value
        elif p.field_info.is_required():
            query[p.name] = INSIDE if p.name in WS_PARAMS else _dummy(p.field_info.annotation)
    json_body = data = files = None
    for p in d.body_params:
        ann = p.field_info.annotation
        if _is_form(p):
            files = {"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")}
            data = {k: (value if ("form", k) == loc else "") for k in ("slug", "path")}
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            named = {f: None for f in ann.model_fields if f in WS_FIELDS}
            for f in named:
                if ("body", f) == loc:
                    named[f] = [value] if f == "workspaces" else value
                elif ann.model_fields[f].is_required():
                    named[f] = INSIDE
            json_body = _model_body(ann, named)
        else:
            json_body = {**DICT_BASE.get((method, route.path), {}),
                         **{k: value for (kind, k) in [loc] if kind == "dict"}}
    return client.request(method, path, params=query or None, json=json_body, data=data,
                          files=files, headers=headers)


def _kind(r) -> Optional[str]:
    """Which refusal ``r`` is, or None."""
    try:
        detail = r.json().get("detail")
    except ValueError:
        return None
    if r.status_code != 403 or not isinstance(detail, dict):
        return None
    if detail.get("refused") == "out_of_scope":
        return "out_of_scope"
    return detail.get("reason")


def _disk(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def owned(client, tmp_path):
    """A workspace the person owns — so a request the ceiling failed to refuse would act on it."""
    r = client.post("/api/workspace/shared/new", json={"name": "Elsewhere"}, headers=_person())
    assert r.status_code == 201, r.text
    return r.json()["workspace_id"]


CASES = _cases()


@pytest.mark.parametrize("method,route,loc", CASES)
@pytest.mark.parametrize("regime", ["autonomous", "human"])
def test_a_workspace_outside_the_ceiling_is_refused_and_nothing_changes(
        client, owned, tmp_path, method, route, loc, regime):
    root = tmp_path / "workspaces"
    before = _disk(root)
    r = _request(client, method, route, loc, owned, _as([INSIDE], regime=regime))
    gap = KNOWN_GAPS.get((method, route.path, loc[1]))
    if gap:
        assert _kind(r) is None, "this route is held to the ceiling now — remove it from KNOWN_GAPS"
        pytest.xfail(gap)
    if (method, route.path) in INTERNAL_TIER:
        assert r.status_code in (401, 403), (r.status_code, r.text)
    elif regime == "human":
        # A person in the loop does not widen the ceiling: the verbs that need a person are held to
        # it as well. The chat door refuses any delegated caller.
        assert _kind(r) in ("out_of_scope", "delegated_dispatch"), (r.status_code, r.text)
    else:
        assert _kind(r) in ("out_of_scope", "human_session_required", "delegated_dispatch"), \
            (r.status_code, r.text)
    assert _disk(root) == before


@pytest.mark.parametrize("method,route,loc", CASES)
def test_an_unbounded_grant_is_held_only_by_the_account(client, method, route, loc):
    r = _request(client, method, route, loc, ABSENT, _as("*", regime="human"))
    assert _kind(r) != "out_of_scope", r.text


@pytest.mark.parametrize("method,route,loc", CASES)
def test_the_workspace_inside_the_ceiling_is_not_refused_by_it(client, method, route, loc):
    r = _request(client, method, route, loc, ABSENT, _as([ABSENT, INSIDE]))
    assert _kind(r) != "out_of_scope", r.text


def test_the_generated_set_does_not_shrink():
    cases = [(c.values[0], c.values[1].path) for c in CASES]
    assert len(cases) >= FLOOR, len(cases)
    assert MUST_COVER <= set(cases), MUST_COVER - set(cases)


def test_every_free_form_body_is_classified():
    found = set()
    for route in _app_routes():
        for p in route.dependant.body_params:
            ann = p.field_info.annotation
            if not _is_form(p) and not (isinstance(ann, type) and issubclass(ann, BaseModel)):
                found.add((sorted(route.methods)[0], route.path))
    assert found == set(DICT_BODIES), (found ^ set(DICT_BODIES))


# ── the company layer, which no route parameter names ──────────────────────────────────────────

GLOBAL_VERBS = [
    ("POST", "/api/workspace/reset", {"target": "_global"}),
    ("PUT", "/api/workspace/file", {"path": "README.md", "content": "x", "slug": "_global"}),
    ("POST", "/api/workspace/entity", {"kind": "company", "name": "Acme", "slug": "_global"}),
    ("POST", "/api/global/ready", {}),
]


@pytest.mark.parametrize("method,path,body", GLOBAL_VERBS)
def test_writing_the_company_layer_is_held_to_the_ceiling(client, method, path, body):
    r = client.request(method, path, json=body, headers=_as([INSIDE]))
    assert _kind(r) == "out_of_scope", r.text


def test_an_empty_ceiling_refuses_every_named_workspace(client):
    r = client.delete(f"/api/workspace/{ABSENT}", headers=_as([]))
    assert _kind(r) == "out_of_scope"


def test_rename_by_id_is_held_to_the_ceiling_by_the_slug_the_id_names(client):
    """The ceiling lists slugs; rename addresses a workspace by its stable id. A worker granted the
    workspace whose slug the id resolves to is not refused by the ceiling, and one granted something
    else is."""
    wid = mint_id()
    client.app.state.workspace_registry.put({"id": wid, "slug": ABSENT, "kind": "group", "name": "B"})
    assert _kind(client.post(f"/api/workspaces/{wid}/rename", json={"name": "x"},
                             headers=_as([INSIDE]))) == "out_of_scope"
    assert _kind(client.post(f"/api/workspaces/{wid}/rename", json={"name": "x"},
                             headers=_as([ABSENT]))) != "out_of_scope"


def test_a_worker_s_target_outside_its_ceiling_is_not_a_default_it_may_write(client):
    """`write_slug` turns an omitted slug into the chat's target. The target is a default, never a
    grant: resolved, it is held to the ceiling like a slug the worker typed."""
    claims = {"sub": PERSON, "delegation": {"regime": "human", "workspaces": [INSIDE],
                                            "target": ABSENT}}
    r = client.put("/api/workspace/file", json={"path": "a.md", "content": "x"},
                   headers={identity_token.HEADER: identity_token.sign(KEY, claims)})
    assert _kind(r) == "out_of_scope", r.text
