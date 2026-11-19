"""A ceiling-bounded worker reads, through the MCP's meeting tools, only meetings inside its ceiling.

The path under test is the one the MCP's `list_meetings` and `get_meeting_transcript` tools take: the
MCP calls the gateway back with the worker's `vxd_` bearer and the identity the gateway signed onto
the worker's `/mcp` request (re-entry), the gateway admits it on the `mcp_reentry` rows and signs the
identity identity answered, and meeting-api grants a read of another member's meeting when it is
bound to one of the identity's `workspaces`.

Identity answers a delegation with the person's memberships narrowed to the dispatch's ceiling, by
the delegation contract's rule (`ceiling_reads`, loaded here by path from
`core/identity/contracts/delegation.v1/delegation.py`, the file identity and agent-api vendor). The
SHIPPED gateway and the SHIPPED meeting-api run in process; the MCP's own hop is the request below.

The person (uid 42) is a member of `ws_alpha` and `ws_beta`; a colleague (uid 9) owns two meetings,
one bound to each. The worker's ceiling is `ws_alpha`.
"""
from __future__ import annotations

import importlib.util
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import create_app as create_gateway
from gateway import identity_token
from gateway.delegation import MCP_REENTRY_HEADER
from meeting_api import create_app as create_meeting_api
from meeting_api.collector.fakes import InMemoryTranscriptStore

from gateway_conformance.gateway_app import _ConformanceDownstream, _NullRedis

_ROOT = pathlib.Path(__file__).resolve().parents[5]
_spec = importlib.util.spec_from_file_location(
    "delegation_contract", _ROOT / "core/identity/contracts/delegation.v1/delegation.py")
delegation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(delegation)

SIGNING_KEY = identity_token.generate_signing_key()
INTERNAL = "conformance-internal-secret"
WORKER = "vxd_worker.token.for-tests"
PERSON_KEY = "vxa_person_key_for_tests"
MEMBERSHIPS = ["ws_alpha", "ws_beta"]
CEILING = ["ws_alpha"]


def _identity_answer(bearer: str):
    """What identity's `/internal/validate` answers: the person for their own key; for the worker,
    the person's memberships narrowed to the ceiling plus the ceiling itself."""
    person = {"user_id": 42, "email": "ada@example.com", "scopes": ["bot", "tx"], "max_concurrent": 3,
              "is_admin": False}
    if bearer == PERSON_KEY:
        return {**person, "workspaces": list(MEMBERSHIPS)}
    if bearer == WORKER:
        return {**person,
                "workspaces": [w for w in MEMBERSHIPS
                               if delegation.ceiling_reads(CEILING, w, subject="42")],
                "delegation": {"regime": "autonomous", "workspaces": list(CEILING)},
                "person_is_admin": False}
    return None


class _Identity:
    async def resolve(self, api_key):
        return _identity_answer(api_key)

    async def authorize_subscribe(self, api_key, meetings):  # pragma: no cover - REST only
        return {"authorized": [], "errors": []}


@pytest.fixture
def world():
    store = InMemoryTranscriptStore()
    seg = lambda i: {"segment_id": f"s{i}", "start": 0.0, "end": 1.0, "text": f"words {i}",
                     "speaker": "Colleague", "language": "en", "completed": True,
                     "absolute_start_time": "2026-06-20T09:00:00Z",
                     "absolute_end_time": "2026-06-20T09:00:01Z"}
    inside = store.seed_meeting(user_id=9, platform="google_meet", native_meeting_id="aaa-bbbb-ccc",
                                status="completed", data={"workspace_id": "ws_alpha"},
                                segments=[seg(1)])
    outside = store.seed_meeting(user_id=9, platform="google_meet", native_meeting_id="ddd-eeee-fff",
                                 status="completed", data={"workspace_id": "ws_beta"},
                                 segments=[seg(2)])
    meeting_api = create_meeting_api(transcript_store=store, identity_key=SIGNING_KEY.public_key(),
                                     internal_secret=INTERNAL)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=meeting_api),
                               base_url="http://meeting-api")
    gateway = create_gateway(_Identity(), _ConformanceDownstream(client), _NullRedis(),
                             identity_key=SIGNING_KEY)
    return TestClient(gateway), inside, outside


def _as_worker():
    """The MCP's tool call: the worker's bearer, plus the identity the gateway signed onto the
    worker's `/mcp` request (re-entry)."""
    marker = identity_token.signed_headers(SIGNING_KEY, _identity_answer(WORKER))[identity_token.HEADER]
    return {"X-API-Key": WORKER, MCP_REENTRY_HEADER: marker}


def _ids(listing) -> set:
    rows = listing.get("meetings", listing) if isinstance(listing, dict) else listing
    return {m.get("id") for m in rows}


def test_identity_answers_the_worker_only_the_memberships_inside_its_ceiling():
    assert _identity_answer(WORKER)["workspaces"] == ["ws_alpha"]


def test_the_person_reads_both_meetings_through_their_memberships(world):
    """The control: the same reads with the person's own key reach both colleague meetings, so the
    refusals below are the ceiling's and not something else."""
    client, inside, outside = world
    listing = client.get("/meetings", headers={"X-API-Key": PERSON_KEY})
    assert listing.status_code == 200, listing.text
    assert {inside, outside} <= _ids(listing.json())
    assert client.get(f"/transcripts/by-id/{outside}", headers={"X-API-Key": PERSON_KEY}).status_code == 200


def test_list_meetings_omits_a_meeting_bound_only_outside_the_ceiling(world):
    client, inside, outside = world
    listing = client.get("/meetings", headers=_as_worker())
    assert listing.status_code == 200, listing.text
    ids = _ids(listing.json())
    assert inside in ids
    assert outside not in ids


def test_get_meeting_transcript_refuses_a_meeting_bound_only_outside_the_ceiling(world):
    """`get_meeting_transcript` by row id is the read a colleague's meeting is reached by (the
    (platform, native) form resolves the caller's own newest row)."""
    client, inside, outside = world
    readable = client.get(f"/transcripts/by-id/{inside}", headers=_as_worker())
    assert readable.status_code == 200, readable.text
    assert client.get(f"/transcripts/by-id/{outside}", headers=_as_worker()).status_code == 404
