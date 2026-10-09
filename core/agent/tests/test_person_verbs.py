"""The verbs that need a person in the loop are data, and one dependency refuses them.

`core/agent/routes.v1.json` declares them as `verbs` rows with `"person": true`;
`route_policy.PERSON_GATE`, installed on the whole app, refuses a worker dispatched without a person
(a delegated identity whose regime is not `human`, an empty regime included) on the route a request
matched, before its handler runs. No route asks for it in its body.

THE CASES ARE GENERATED from the built app and the manifest. Every flagged route is called as an
unwatched worker whose ceiling GRANTS the workspace it names — so nothing but this check stands in
the way — and must be refused with nothing changed on disk. A worker with a person in the loop and a
person's own client are not refused by it. The flagged set must hold every verb in `EXPECTED` and
every destructive or membership-changing route the app serves, so dropping a flag fails here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from control_plane import identity_token, route_policy
from tests.test_delegation_ceiling_verbs import (
    DICT_BASE, PERSON, WS_FIELDS, WS_PARAMS, _app_routes, _as, _build, _disk, _is_form, _kind,
    _model_body, _person)

AGENT = Path(__file__).resolve().parents[1]
REFUSED = identity_token.REFUSAL["reason"]

#: The verbs that must need a person. The manifest may flag more; it may never flag fewer.
EXPECTED = frozenset({
    # destroying, resetting or hiding a workspace
    ("DELETE", "/api/workspace/{slug}"), ("POST", "/api/workspace/reset"),
    ("POST", "/api/workspace/{slug}/archive"),
    # who a workspace is shared with
    ("POST", "/api/workspace/{slug}/share-enable"), ("POST", "/api/workspace/{workspace_id}/unshare"),
    ("POST", "/api/workspace/invites"), ("DELETE", "/api/workspace/invites/{invite_id}"),
    ("POST", "/api/workspace/invites/accept"),
    ("POST", "/api/workspace/invite"), ("POST", "/api/workspace/membership"),
    ("POST", "/api/workspace/members/{member_subject}/role"),
    ("DELETE", "/api/workspace/members/{member_subject}"),
    ("POST", "/api/workspace/{workspace_id}/leave"),
    # git credentials, and where a tree is loaded from or syncs to
    ("POST", "/api/workspace/git-token"), ("POST", "/api/workspace/{slug}/deploy-key"),
    ("POST", "/api/workspace/swap"), ("POST", "/api/workspace/activate"),
    ("POST", "/api/workspace/import"), ("POST", "/api/workspace/publish"),
    ("POST", "/api/workspace/push"), ("POST", "/api/workspace/pull"),
    ("POST", "/api/workspace/git-remote-detach"),
    ("POST", "/api/workspace/shared/{workspace_id}/attach"),
    # the person's own word on a claim (`validate`)
    ("POST", "/api/claims/verdicts"),
    # a dispatch armed for later
    ("POST", "/api/routines"), ("POST", "/api/routines/{name}/confirm"),
    ("PATCH", "/api/routines/{name}/enabled"), ("DELETE", "/api/routines/{routine_id}"),
    # a mailbox, a calendar, a stored credential, a consent request, the person's clock
    ("POST", "/api/connections/request"), ("POST", "/api/connections/read"),
    ("POST", "/api/connections/gmail/search"), ("POST", "/api/connections/gmail/inbox"),
    ("POST", "/api/connections/gmail/read"), ("POST", "/api/connections/gmail/thread"),
    ("POST", "/api/connections/calendar/events"), ("POST", "/api/connections/gmail/draft"),
    ("POST", "/api/connections/service/call"), ("POST", "/api/onboarding/research"),
    ("PUT", "/api/time/zone"),
})
#: A non-GET route whose path has one of these segments changes who a workspace is shared with, or
#: what credential reaches it, and must be flagged — a new one included, the day it lands.
MEMBERSHIP_SEGMENTS = frozenset({"members", "invites", "invite", "membership", "leave", "unshare",
                                 "share-enable", "archive", "deploy-key", "git-token"})
#: Destructive routes a delegated caller can never reach, so this check is not theirs: the internal
#: tier only (a worker never holds it).
NOT_A_WORKER_DOOR = frozenset({("POST", "/api/workspace/git/reset")})
#: A body that would act on the person's own things if the gate let it through.
BODIES = {
    ("POST", "/api/workspace/reset"): {"target": "personal"},
    ("POST", "/api/workspace/git-token"): {"token": ""},
}

ROUTES = {(m, r.path): r for r in _app_routes() for m in r.methods}
CASES = sorted(route_policy.PERSON_VERBS)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A person with a desk and a shared workspace they own: what a verb let through would act on."""
    client = _build(monkeypatch, tmp_path)
    r = client.put("/api/workspace/file", json={"path": "notes.md", "content": "mine"}, headers=_person())
    assert r.status_code == 200, r.text
    r = client.post("/api/workspace/shared/new", json={"name": "Elsewhere"}, headers=_person())
    assert r.status_code == 201, r.text
    return client, r.json()["workspace_id"], tmp_path / "workspaces"


def _acting(client, key, owned, headers):
    """``key`` called as it would be to ACT: every workspace it names is ``owned``, every other
    field a value it accepts."""
    method, path = key
    route = ROUTES[key]
    d = route.dependant
    for p in d.path_params:
        path = path.replace("{" + p.name + "}", owned if p.name in WS_PARAMS else
                            (PERSON if p.name == "member_subject" else "x1"))
    query = {p.name: owned for p in d.query_params if p.name in WS_PARAMS}
    body = None
    for p in d.body_params:
        ann = p.field_info.annotation
        assert not _is_form(p), "a person verb that takes a form needs a case here"
        if key in BODIES:
            body = BODIES[key]
        elif isinstance(ann, type) and hasattr(ann, "model_fields"):
            named = {f: owned for f in ann.model_fields if f in WS_FIELDS and f != "workspaces"}
            try:
                body = _model_body(ann, named)
            except ValueError:   # a constrained field the generic dummy misses: still well-formed JSON,
                body = {f: named.get(f, "x") for f, fi in ann.model_fields.items()   # refused first
                        if f in named or fi.is_required()}
        else:
            body = dict(DICT_BASE.get(key, {}))
    return client.request(method, path, params=query or None, json=body, headers=headers)


@pytest.mark.parametrize("key", CASES, ids=[f"{m} {p}" for m, p in CASES])
@pytest.mark.parametrize("regime", ["autonomous", ""])
def test_an_unwatched_worker_is_refused_and_nothing_changes(world, key, regime):
    client, owned, root = world
    before = _disk(root)
    r = _acting(client, key, owned, _as([owned], regime=regime))
    assert r.status_code == 403 and r.json()["detail"] == identity_token.REFUSAL, r.text
    assert _disk(root) == before


@pytest.mark.parametrize("key", CASES, ids=[f"{m} {p}" for m, p in CASES])
@pytest.mark.parametrize("who", ["human", "person"])
def test_a_person_in_the_loop_is_not_refused_by_it(monkeypatch, tmp_path, key, who):
    """Called with a body no route accepts and naming nothing that exists, so the route answers
    without acting; all that matters is that the answer is not this refusal."""
    client = _build(monkeypatch, tmp_path)
    method, path = key
    path = re.sub(r"\{[^}]+\}", "nothing-here", path)
    headers = _as("*", regime="human") if who == "human" else _person()
    has_body = bool(ROUTES[key].dependant.body_params)
    r = client.request(method, path, json=[] if has_body else None, headers=headers)
    assert _kind(r) != REFUSED, (r.status_code, r.text)


def test_every_declared_verb_is_a_route_the_app_serves():
    route_policy.assert_served([r for r in ROUTES.values()])
    assert set(CASES) <= set(ROUTES)


def test_the_flagged_set_holds_every_expected_verb():
    assert EXPECTED <= route_policy.PERSON_VERBS, sorted(EXPECTED - route_policy.PERSON_VERBS)


def test_every_destructive_or_membership_route_is_flagged():
    family = {(m, p) for (m, p) in ROUTES if m not in ("GET", "HEAD")
              and (m == "DELETE" or MEMBERSHIP_SEGMENTS & set(p.split("/")))} - NOT_A_WORKER_DOOR
    assert family <= route_policy.PERSON_VERBS, sorted(family - route_policy.PERSON_VERBS)


def test_no_route_asks_for_a_person_by_hand():
    """The flag is the one declaration. A `require_person` call in a router body is a second one,
    and a fail-open default for it (`require_person or (lambda request: None)`) a hole."""
    for f in sorted((AGENT / "control_plane" / "routers").glob("*.py")):
        src = f.read_text()
        assert "require_person" not in src, f.name


def test_the_manifest_rows_map_onto_agent_api_routes():
    doc = json.loads((AGENT / "routes.v1.json").read_text())
    keys = route_policy.person_verbs(doc)
    assert keys == route_policy.PERSON_VERBS
    assert all(p.startswith("/api/") for _, p in keys)


def _doc(rows, **extra):
    return {"forward": {"edge_prefix": "/agent/", "upstream_prefix": "/api/"}, "verbs": rows, **extra}


@pytest.mark.parametrize("doc,needle", [
    ({"forward": {"edge_prefix": "/agent/", "upstream_prefix": "/api/"}}, "no `verbs` list"),
    (_doc([{"method": "POST", "path": "/agent/x", "person": "yes"}]), "true or false"),
    (_doc([{"method": "POST", "path": "/meetings/x", "person": True}]), "outside the forward"),
    (_doc([{"method": "FETCH", "path": "/agent/x", "person": True}]), "not an HTTP method"),
    (_doc([{"method": "POST", "path": "/agent/x", "person": True}] * 2), "declared twice"),
    ({"verbs": []}, "no forward"),
])
def test_a_malformed_declaration_refuses_the_boot(doc, needle):
    with pytest.raises(route_policy.PolicyError) as e:
        route_policy.person_verbs(doc)
    assert needle in str(e.value)


def test_a_row_naming_no_served_route_refuses_the_boot(monkeypatch):
    monkeypatch.setattr(route_policy, "PERSON_VERBS",
                        route_policy.PERSON_VERBS | {("POST", "/api/workspace/no-such-verb")})
    with pytest.raises(route_policy.PolicyError) as e:
        route_policy.assert_served(list(ROUTES.values()))
    assert "/api/workspace/no-such-verb" in str(e.value)
