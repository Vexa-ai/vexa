"""The route table is a contract, and route ORDER must not be part of it.

`api.py` used to register all 78 routes in one 2,868-line `create_app`. They now live in
`control_plane/routers/`, one module per owner — which necessarily changes the order they are
registered in, and FastAPI resolves **first-match-wins**. So the regrouping is only safe while no
two routes can match the same concrete URL under the same method.

That was true when the split was made. This file is what keeps it true: a route added later whose
pattern overlaps another one would make include order a behaviour, silently, and the failure would
be a request answered by the wrong handler rather than an error anybody sees.
"""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _effective(routes, prefix=""):
    """The routes a REQUEST sees. FastAPI 0.138 does not flatten `include_router` into
    `app.routes`; it leaves an `_IncludedRouter` holding the original router plus an include
    context, and resolves at match time. Reading `app.routes` alone finds seven placeholders and
    no routes — which is exactly the shape that would make this test pass by testing nothing."""
    out = []
    for r in routes:
        inc = getattr(r, "include_context", None)
        if inc is not None:
            out.extend(_effective(r.original_router.routes,
                                  prefix + (getattr(inc, "prefix", "") or "")))
        elif getattr(r, "path", None):
            out.append((prefix + r.path, frozenset(getattr(r, "methods", ()) or ()), r.name))
    return out


@pytest.fixture(scope="module")
def routes(tmp_path_factory):
    root = tmp_path_factory.mktemp("rt")
    app = create_app(Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(),
                                _FakeIdentity()), reader=WorkspaceReader(str(root)))
    with TestClient(app):
        pass
    return _effective(app.routes)


def _overlap(a: str, b: str) -> bool:
    """Can one concrete URL match both patterns? A `{param}` matches one segment and never a `/`,
    so different segment counts can never collide."""
    sa = [None if s.startswith("{") and s.endswith("}") else s for s in a.strip("/").split("/")]
    sb = [None if s.startswith("{") and s.endswith("}") else s for s in b.strip("/").split("/")]
    return len(sa) == len(sb) and all(x is None or y is None or x == y for x, y in zip(sa, sb))


def test_no_two_routes_can_match_the_same_url(routes):
    clashes = [
        (sorted(ma & mb), pa, na, pb, nb)
        for (pa, ma, na), (pb, mb, nb) in itertools.combinations(routes, 2)
        if pa != pb and (ma & mb) and _overlap(pa, pb)
    ]
    assert clashes == [], (
        "these route pairs can match the same URL, so the ORDER they are registered in decides "
        "which handler answers — and that order is now the order routers are included in "
        f"`create_app`, not the order they appear in one file: {clashes}")


def test_the_routers_are_included_without_a_prefix_or_an_injected_dependency(routes):
    """`include_router(..., prefix=…)` or `dependencies=[…]` would move every path or add an auth
    check invisibly — a behaviour change that no handler's source would show."""
    app_paths = {p for p, _m, _n in routes}
    assert "/health" in app_paths and "/api/chat" in app_paths
    assert not any(p.startswith("//") for p in app_paths)


def test_every_route_still_has_a_name_the_tests_and_the_schema_use(routes):
    names = [n for _p, _m, n in routes]
    assert len(names) == len(set(names)), "two routes share a handler name"
    assert all(names), "a route lost its handler name in the move"
