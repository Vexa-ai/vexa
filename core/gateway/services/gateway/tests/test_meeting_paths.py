"""A meetings row forwards to exactly the meeting-api route its manifest row names, and nowhere else.

The meeting routes carry the caller's `platform` and `native_meeting_id` in the path. Starlette hands a
handler its parameters percent-DECODED, so a value interpolated raw into the hop could carry a `..`,
a `?` or a `#` that reshapes the downstream URL — and land the request on a meeting-api route other
than the one whose scope the edge checked. Every meetings row is therefore forwarded through one
helper (`_meeting_target`): the target is the row's own path or its manifest `upstream`, `platform`
must be a meeting platform (api.v1 `Platform`; anything else is the 422 api.v1 declares), and each
parameter is the forwarded-row rule — a `.`/`..` value or an encoded `/` or `\\` is a 400 before the
caller is authorized, everything else is percent-encoded into the segment it arrived in.

The hostile requests are driven into the ASGI app with the raw target a real server hands it:
TestClient (httpx) normalises dot segments out of a URL before it sends one.
"""
from __future__ import annotations

import asyncio

import json
import pathlib
import typing
from urllib.parse import quote, unquote

import httpx
import pytest

from gateway import create_app, routes_manifest
from gateway.app import MeetingPlatform
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

_ROOT = routes_manifest._repo_root()
_MEETINGS = json.loads((_ROOT / "core/meetings/routes.v1.json").read_text())
_ROWS = [(r["method"], r["path"], r.get("upstream", r["path"])) for r in _MEETINGS["routes"]]
#: The rows whose path carries a free-text parameter the caller writes: `platform` and/or
#: `native_meeting_id` (the int-typed ids are coerced by FastAPI and cannot carry text).
_TEXT_ROWS = [(m, p, u) for (m, p, u) in _ROWS
              if "{platform}" in p or "{native_meeting_id}" in p]
_VALUES = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "meeting_id": "12",
           "recording_id": "34", "media_file_id": "56", "calendar_id": "work-1"}


def _fill(template: str, **override: str) -> str:
    values = {**_VALUES, **override}
    for name in routes_manifest.params_of(template):
        template = template.replace("{" + name + "}", values[name], 1)
    return template


def _app(user=None):
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user), downstream, FakeRedis())
    return app, downstream


async def _send(app, method: str, raw_target: str) -> int:
    """One request, as uvicorn would deliver it: `path` is the decoded target, dot segments and
    all; `raw_path` is the bytes on the wire."""
    raw = raw_target.encode("latin-1")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "root_path": "", "query_string": b"",
        "path": unquote(raw_target), "raw_path": raw,
        "headers": [(b"host", b"testserver"), (b"x-api-key", VALID_KEY.encode()),
                    (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    sent = []
    delivered = []

    async def receive():
        # The body once, then nothing until the server is done — what uvicorn does. A streamed
        # response listens for a disconnect while it sends, and must not be handed the body again.
        if not delivered:
            delivered.append(True)
            return {"type": "http.request", "body": b"{}", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _hop(downstream) -> httpx.URL:
    """The forwarded URL as httpx would send it (dot segments resolved, fragment dropped)."""
    return httpx.URL(downstream.last["url"])


# ── one source of truth ──────────────────────────────────────────────────────────────────────────

def test_the_platform_set_is_api_v1_s():
    api = json.loads((_ROOT / "core/gateway/contracts/api.v1/api.schema.json").read_text())
    assert set(typing.get_args(MeetingPlatform)) == set(api["components"]["schemas"]["Platform"]["enum"])


@pytest.mark.parametrize("method,path,upstream", _ROWS)
async def test_every_meetings_row_forwards_to_the_route_its_manifest_row_names(method, path, upstream):
    """Generated over the manifest: the hop is the row's own path, or its `upstream`, filled with
    what was matched — so meeting-api, which reads the same rows, checks the scopes of the row that
    really reached it."""
    app, downstream = _app()
    assert await _send(app, method, _fill(path)) < 500
    assert downstream.last is not None, (method, path)
    assert downstream.last["method"] == method
    assert downstream.last["url"] == "http://meeting-api" + _fill(upstream)


# ── R4-14: every caller-written parameter is one opaque segment, or refused ─────────────────────

#: Values that would reshape the hop if interpolated raw, written as they go on the wire. Each is
#: refused before any forward: 400 where the row still matches, and 404 where an encoded `/`, once
#: Starlette decodes it, leaves the path matching no route at all.
_REFUSED_NATIVE = ["..", "%2E%2E", "%2e%2e", ".%2E", ".", "%2E", "a%2Fb", "a%2fb", "%2F..%2F..%2Fbots",
                   "a%5Cb", "%5C..", "x%2F%2E%2E"]
#: Values that stay data: forwarded, percent-encoded, in the segment they arrived in.
_KEPT_NATIVE = {"x%3Fdebug%3D1": "x?debug=1", "x%23frag": "x#frag", "bots%23": "bots#",
                "%3F": "?", "%23": "#", "a%20b": "a b", "..x": "..x", "x..": "x.."}


@pytest.mark.parametrize("value", _REFUSED_NATIVE)
@pytest.mark.parametrize("method,path,upstream", [r for r in _TEXT_ROWS if "{native_meeting_id}" in r[1]])
async def test_a_dot_or_encoded_separator_native_id_is_400_before_any_forward(method, path, upstream, value):
    app, downstream = _app()
    status = await _send(app, method, _fill(path, native_meeting_id=value))
    assert status in ((400, 404) if "%2f" in value.lower() else (400,)), status
    assert downstream.last is None


@pytest.mark.parametrize("value,decoded", sorted(_KEPT_NATIVE.items()))
@pytest.mark.parametrize("method,path,upstream", [r for r in _TEXT_ROWS if "{native_meeting_id}" in r[1]])
async def test_a_query_or_fragment_character_stays_inside_its_segment(method, path, upstream, value, decoded):
    app, downstream = _app()
    assert await _send(app, method, _fill(path, native_meeting_id=value)) < 500
    hop = _hop(downstream)
    assert downstream.last["method"] == method
    assert hop.query == b"" and hop.fragment == ""
    expected = _fill(upstream, native_meeting_id=quote(decoded, safe=""))
    assert hop.raw_path.decode("ascii") == expected
    assert len(hop.path.split("/")) == len(upstream.split("/"))


@pytest.mark.parametrize("value", ["..", "%2E%2E", ".", "platform", "bots", "x%23", "x%3F", "a%2Fb",
                                   "Google_Meet", "google_meet%20", "unknown"])
@pytest.mark.parametrize("method,path,upstream", [r for r in _TEXT_ROWS if "{platform}" in r[1]])
async def test_a_platform_that_is_not_a_meeting_platform_is_refused_before_any_forward(
        method, path, upstream, value):
    app, downstream = _app()
    assert await _send(app, method, _fill(path, platform=value)) in (400, 404, 422)
    assert downstream.last is None


@pytest.mark.parametrize("platform", typing.get_args(MeetingPlatform))
async def test_every_meeting_platform_is_forwarded(platform):
    app, downstream = _app()
    assert await _send(app, "GET", f"/transcripts/{platform}/abc") < 500
    assert downstream.last["url"] == f"http://meeting-api/transcripts/{platform}/abc"


# ── the cross-scope hop ──────────────────────────────────────────────────────────────────────────

TX_ONLY = {"user_id": 7, "scopes": ["tx"], "max_concurrent": 1}


@pytest.mark.parametrize("target", [
    "/meetings/%2E%2E/%23/annotate",
    "/meetings/../%23/annotate",
    "/meetings/google_meet/..%2F..%2Fbots%23/annotate",
    "/meetings/google_meet/%2E%2E/annotate",
    "/meetings/google_meet/bots%23/annotate",
    "/meetings/google_meet/%3F/annotate",
    "/meetings/google_meet/x%2F..%2F..%2F..%2Fbots/annotate",
])
async def test_a_tx_key_on_a_tx_route_never_reaches_a_bot_route(target):
    """The shape this closes: a `tx` row matched at the edge (annotate), with path values that
    would have resolved the hop onto meeting-api's `POST /bots` (scope `bot`). Each is refused, or
    forwarded to the annotate route it matched and nowhere else."""
    app, downstream = _app(user=TX_ONLY)
    status = await _send(app, "POST", target)
    if downstream.last is None:
        assert status in (400, 404, 422), status
        return
    hop = _hop(downstream)
    assert hop.query == b"" and hop.fragment == ""
    parts = hop.path.split("/")
    assert parts[1] == "meetings" and parts[2] == "google_meet" and parts[-1] == "annotate", hop
    assert len(parts) == 5, hop
