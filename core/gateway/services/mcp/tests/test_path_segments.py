"""Every caller-supplied value this service puts into a URL path is ONE segment of it, and nothing else.

A meeting tool addresses the gateway by `/<family>/{platform}/{native_meeting_id}/…`, and both halves
come from the caller. Interpolated raw, a value is a fragment of URL: `/` ends its segment, `?` and
`#` push the rest into a query string or fragment the route never reads, and a `.`/`..` segment is
RESOLVED by httpx before the request leaves — `stop_bot(native_meeting_id="..")` would have gone
out as `DELETE /bots`. One helper (`vexa_mcp/paths.py`) decides what a value becomes, and every
place that builds a path uses it. These tests read the URL the service actually SENT.
"""
from __future__ import annotations

from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vexa_mcp import bind, register
from vexa_mcp import manifest as m
from vexa_mcp.paths import path_segment

#: Each one, interpolated raw, changes the SHAPE of the path or leaves it.
TRICKY = ["a/b", "..", ".", "%2F", "%2e%2e", "a?b=1", "a#frag", "../../admin", "a b", "x/../../y"]


@pytest.mark.parametrize("value,expected", [
    ("abc-defg-hij", "abc-defg-hij"),
    ("a/b", "a%2Fb"),
    ("..", "%2E%2E"),
    (".", "%2E"),
    ("...", "%2E%2E%2E"),
    ("%2F", "%252F"),
    ("a?b", "a%3Fb"),
    ("a#b", "a%23b"),
    ("a.b", "a.b"),
    (42, "42"),
])
def test_path_segment_encodes_everything_that_could_change_the_path(value, expected):
    assert path_segment(value) == expected


def _path_and_rest(request: httpx.Request):
    """The path the service sent, as raw bytes, split from any query; and the fragment."""
    raw = request.url.raw_path.decode("ascii")
    path, _, query = raw.partition("?")
    return path, query, request.url.fragment


def _segments(path: str):
    return path.split("/")[1:]


#: tool path on THIS service, method, extra kwargs, the gateway path it must send (id as `{id}`).
HAND_WRITTEN = [
    ("GET", "/meeting-transcript", {}, "/transcripts/google_meet/{id}"),
    ("DELETE", "/bot", {}, "/bots/google_meet/{id}"),
    ("PUT", "/bot-config", {"json": {"language": "es"}}, "/bots/google_meet/{id}/config"),
    ("POST", "/meeting-speak", {"json": {"text": "hi", "asked_by_a_human": True}},
     "/bots/google_meet/{id}/speak"),
    ("GET", "/meeting-chat", {}, "/bots/google_meet/{id}/chat"),
    ("POST", "/meeting-annotate", {"json": {"title": "t"}}, "/meetings/google_meet/{id}/annotate"),
]


@pytest.mark.parametrize("value", TRICKY)
@pytest.mark.parametrize("method,tool,kwargs,expected", HAND_WRITTEN)
def test_a_meeting_id_is_one_segment_of_the_path_a_hand_written_tool_sends(
        client, gateway, auth, method, tool, kwargs, expected, value):
    r = client.request(method, tool, params={"platform": "google_meet", "native_meeting_id": value},
                       headers=auth, **kwargs)
    assert r.status_code == 200, r.text
    path, query, fragment = _path_and_rest(gateway.requests[-1])
    want = _segments(expected)
    got = _segments(path)
    assert len(got) == len(want), (value, path)
    at = want.index("{id}")
    assert [s for i, s in enumerate(got) if i != at] == [s for s in want if s != "{id}"], path
    assert unquote(got[at]) == value, (value, path)
    assert got[at] not in (".", ".."), "a dot segment would be resolved before it left"
    assert query == "" and fragment == "", (value, query, fragment)


@pytest.mark.parametrize("value", TRICKY)
def test_the_room_and_the_id_both_stay_in_their_segments(client, gateway, auth, value):
    """The platform half is from a closed set; the id half carries anything, as data."""
    client.get("/meeting-transcript", params={"platform": "teams", "native_meeting_id": value},
               headers=auth)
    path, query, _ = _path_and_rest(gateway.requests[-1])
    assert _segments(path)[:2] == ["transcripts", "teams"] and len(_segments(path)) == 3, path
    assert query == ""


@pytest.mark.parametrize("platform", ["..", "google_meet/..", "zoom?x=1", "webex", "%2E%2E", "."])
def test_a_platform_outside_the_known_set_is_refused_before_any_hop(client, gateway, auth, platform):
    before = len(gateway.requests)
    for method, tool, kwargs, _ in HAND_WRITTEN:
        r = client.request(method, tool, params={"platform": platform, "native_meeting_id": "abc"},
                           headers=auth, **kwargs)
        assert r.status_code == 422, (tool, platform, r.status_code)
        assert "unknown platform" in str(r.json()), (tool, r.json())
    assert len(gateway.requests) == before


def test_the_caller_is_told_the_pair_it_sent_not_the_encoded_one(client, gateway, auth):
    """Encoding is a property of the URL, never of the answer: the echo carries the id as sent."""
    gateway.routes[("POST", "/meetings/google_meet/a%2Fb/annotate")] = (200, {})
    r = client.post("/meeting-annotate", params={"native_meeting_id": "a/b"},
                    json={"title": "t"}, headers=auth)
    assert r.json()["native_meeting_id"] == "a/b"


# --- the generic builder: an assembled tool's path parameters ------------------------------------

_MANIFEST = {
    "contract": "mcp.tools.v1", "domain": "flows", "source": "oss", "owner": "core/flows",
    "base_url_env": "FLOWS_API_URL", "served_at": "/.well-known/mcp-tools.json",
    "depends_on": ["identity"],
    "tools": [{"name": "reaction_signal", "identity": "user", "auth": "subject",
               "requires": ["identity", "flows"],
               "route": {"method": "POST", "path": "/reactions/{reaction_id}/{verb}"}}],
}
_OPENAPI = {"paths": {"/reactions/{reaction_id}/{verb}": {"post": {"summary": "steer", "parameters": []}}}}
_FORWARDED = {**_MANIFEST, "domain": "agent", "owner": "core/agent", "base_url_env": "AGENT_API_URL",
              "forward": {"edge_prefix": "/agent/", "upstream_prefix": "/api/"},
              "tools": [{**_MANIFEST["tools"][0], "requires": ["identity", "agent"],
                         "route": {"method": "POST", "path": "/api/reactions/{reaction_id}/{verb}"}}]}


def _assembled(doc, openapi, gateway_url=None):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    app = FastAPI()
    domain = doc["domain"]
    bound = bind.verify(m.assemble([doc], deployed={"identity", domain}), {domain: openapi})
    register.register(app, bound, {domain: f"http://{domain}-door"},
                      transport=httpx.MockTransport(handler), gateway_url=gateway_url)
    return TestClient(app), seen


@pytest.mark.parametrize("value", TRICKY)
@pytest.mark.parametrize("doc,openapi,gateway_url,prefix", [
    (_MANIFEST, _OPENAPI, None, ["reactions"]),
    (_FORWARDED, {"paths": {"/api/reactions/{reaction_id}/{verb}": _OPENAPI["paths"][
        "/reactions/{reaction_id}/{verb}"]}}, "http://gateway.test", ["agent", "reactions"]),
])
def test_an_assembled_tool_s_path_parameter_is_one_segment_at_its_door_or_the_gateway(
        doc, openapi, gateway_url, prefix, value):
    client, seen = _assembled(doc, openapi, gateway_url)
    r = client.post("/tools/reaction_signal", params={"reaction_id": value, "verb": "retry"},
                    headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    path, query, fragment = _path_and_rest(seen[-1])
    got = _segments(path)
    assert got[:len(prefix)] == prefix and got[-1] == "retry" and len(got) == len(prefix) + 2, path
    assert unquote(got[len(prefix)]) == value and got[len(prefix)] not in (".", "..")
    assert query == "" and fragment == ""
