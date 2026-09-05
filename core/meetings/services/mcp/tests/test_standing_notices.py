"""STANDING NOTICES ride out on the meeting tools' results — what an agent reads without asking.

An agent reads a tool result and nothing else unless something makes it. So a fact that stays true
between calls travels attached to the call the agent just made, or it travels on a tool somebody
has to remember to call. This file pins the ride, and every one of its claims is a failure that
would otherwise be silent:

  S1  the meeting tools carry them — field in the body, trailing line in the text, once per result
  S2  the tools that are not about a meeting do not, and `whats_waiting` never does (it already says it)
  S3  no notices, no flows domain, a slow domain, a broken answer → the result is exactly the result
  S4  a REFUSAL carries them too — the same trailing line, once, the refusal itself intact (#1549)
  S5  the surface is unchanged: no new tool, no new argument

Autonomous, like the rest of the suite: `create_app` takes the transport as an injected port, so a
fake stands in for both the gateway and the notices hop and the SHIPPED path runs with no network.

FIXTURE-LOCAL WORDS ONLY. Nothing in this file is any deployment's copy, and this module knows no
vocabulary at all — a notice is a string it was handed.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import API_KEY, GATEWAY_URL
from vexa_mcp import create_app, notices
from vexa_mcp.tool_errors import render_tool_error

FLOWS_URL = "http://flows.test"
STANDING = "A fixture sentence that stays true between calls."
SECOND = "A second fixture sentence."
MEETING_URL = "https://meet.google.com/abc-defg-hij"


def _app(*, body=None, status=200, flows_url=FLOWS_URL, delay=False, gateway=None):
    """An MCP app whose flows domain answers `body` on `/queue/notices`.

    ASSEMBLY IS OFF: this test is about the notice ride, and assembly would make the boot fetch a
    manifest and an OpenAPI from the same fake. `FLOWS_API_URL` still reaches `notices` because it
    reads the env it was handed, which is the same env assembly reads.
    """
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == notices.NOTICES_PATH:
            if delay:
                raise httpx.TimeoutException("slow", request=request)
            return httpx.Response(status, json=body if body is not None else {})
        if gateway is not None:
            return gateway(request)
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    env = {"VEXA_MCP_ASSEMBLY_OFF": "1"}
    if flows_url:
        env["FLOWS_API_URL"] = flows_url
    return create_app(GATEWAY_URL, transport=httpx.MockTransport(upstream), assembly_env=env)


@pytest.fixture
def agent():
    """A live `/mcp` session against an app whose flows domain has one standing notice — the
    session shape `test_denial_reaches_the_agent.py` documents."""
    def session(app):
        ctx = TestClient(app)
        client = ctx.__enter__()
        head = {"Authorization": f"Bearer {API_KEY}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}
        r = client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}})
        head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        client.post("/mcp", headers=head,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        def call(name, **arguments):
            got = client.post("/mcp", headers=head, json={
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}})
            return got.json()["result"]
        call.close = lambda: ctx.__exit__(None, None, None)
        return call
    return session


def _text(result) -> str:
    return "\n".join(c["text"] for c in result["content"] if c.get("type") == "text")


# ── S1 · the ride ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,arguments", [
    ("get_bot_status", {}),
    ("list_meetings", {}),
    ("stop_bot", {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}),
    ("get_meeting_transcript", {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}),
    ("request_meeting_bot", {"meeting_url": MEETING_URL}),
])
def test_every_meeting_tool_carries_the_notice(agent, tool, arguments):
    """S1. The list is the point: an agent working on a person's meetings reads a standing fact
    about that person's account whichever of these it happened to call."""
    call = agent(_app(body={"notices": [STANDING]}))
    try:
        assert STANDING in _text(call(tool, **arguments))
    finally:
        call.close()


def test_the_notice_is_in_the_body_as_a_field_and_in_the_text_as_a_line(agent):
    """BOTH CHANNELS, because neither is reliably the one an agent looks at: the field for a caller
    that parses the body, the trailing line for one that reads the text."""
    call = agent(_app(body={"notices": [STANDING]}))
    try:
        text = _text(call("get_bot_status"))
        assert f"{notices.PREFIX}{STANDING}" in text, "the trailing line"
        body = json.loads(text.split(f"\n\n{notices.PREFIX}")[0])
        assert body["notices"] == [STANDING], "the field"
    finally:
        call.close()


def test_the_original_answer_survives_intact(agent):
    """A notice is ADDITIVE. Everything the tool answered is still there, unchanged."""
    def gateway(request):
        return httpx.Response(200, json={"running_bots": [{"id": 7}], "count": 1})

    call = agent(_app(body={"notices": [STANDING]}, gateway=gateway))
    try:
        body = json.loads(_text(call("get_bot_status")).split(f"\n\n{notices.PREFIX}")[0])
        assert body["running_bots"] == [{"id": 7}] and body["count"] == 1
    finally:
        call.close()


def test_a_notice_appears_once_per_result(agent):
    call = agent(_app(body={"notices": [STANDING]}))
    try:
        assert _text(call("get_bot_status")).count(STANDING) == 2, "the field, then the line"
    finally:
        call.close()


def test_two_notices_are_two_lines_in_order(agent):
    call = agent(_app(body={"notices": [STANDING, SECOND]}))
    try:
        text = _text(call("get_bot_status"))
        assert text.index(f"{notices.PREFIX}{STANDING}") < text.index(f"{notices.PREFIX}{SECOND}")
    finally:
        call.close()


def test_the_hop_forwards_the_caller_s_own_credential_and_holds_none(agent):
    """The notices route is subject-scoped: it answers for whoever the credential resolves to. This
    edge holds no credential of its own and must never substitute one."""
    seen = []

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == notices.NOTICES_PATH:
            seen.append(request.headers.get("x-api-key"))
            return httpx.Response(200, json={"notices": [STANDING]})
        return httpx.Response(200, json={"ok": True})

    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(upstream),
                     assembly_env={"VEXA_MCP_ASSEMBLY_OFF": "1", "FLOWS_API_URL": FLOWS_URL})
    call = agent(app)
    try:
        call("get_bot_status")
        assert seen == [API_KEY]
    finally:
        call.close()


# ── S2 · which tools, and which do not ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,arguments", [
    ("parse_meeting_link", {"meeting_url": MEETING_URL}),
])
def test_a_tool_that_reaches_no_meeting_carries_nothing(agent, tool, arguments):
    """S2. A notice on a pure parse is noise on a call that touched nothing of the person's."""
    call = agent(_app(body={"notices": [STANDING]}))
    try:
        assert STANDING not in _text(call(tool, **arguments))
    finally:
        call.close()


def test_the_queue_tool_is_not_in_the_carrying_set():
    """`whats_waiting` answers with every waiting item, notices among them. Attaching them again
    would say the same sentence twice in one result."""
    assert "whats_waiting" not in notices.CARRIES_NOTICES


# ── S3 · never an error ───────────────────────────────────────────────────────────────────────

def test_no_notices_means_the_result_is_byte_for_byte_the_result(agent):
    plain = agent(_app(body={"notices": []}))
    try:
        text = _text(plain("get_bot_status"))
        assert notices.PREFIX not in text
        assert "notices" not in json.loads(text)
    finally:
        plain.close()


@pytest.mark.parametrize("kwargs", [
    {"flows_url": ""},                       # this deployment carries no flows domain
    {"delay": True},                         # the domain is there and slow
    {"status": 500},                         # the domain answered badly
    {"status": 401},                         # the credential does not open that door
    {"body": {"notices": "not a list"}},     # the answer is not the shape this expects
    {"body": {"notices": [{"say": "an object"}]}},
    {"body": {}},                            # no field at all
])
def test_nothing_that_can_go_wrong_reaches_the_agent(agent, kwargs):
    """S3, and it is the whole safety claim: a notice is EXTRA. Whatever happens on that hop, the
    call the agent actually made answers exactly as it would have."""
    call = agent(_app(**kwargs))
    try:
        result = call("get_bot_status")
        assert result.get("isError") is not True
        assert notices.PREFIX not in _text(result)
    finally:
        call.close()


# ── S4 · a refusal is a result too (#1549) ────────────────────────────────────────────────────

REFUSAL = {"code": "insufficient_balance", "reason": "a_fixture_reason",
           "message": "A fixture message the decider authored.",
           "action_url": "https://example.invalid/account"}


def _refusing(status=403, detail=None):
    def gateway(request):
        return httpx.Response(status, json={"detail": detail if detail is not None else REFUSAL})
    return gateway


def test_a_refusal_carries_the_notice_too(agent):
    """S4, and the measured defect: a 403 reached the agent with reason, message and action_url and
    WITHOUT the standing sentence that said why. A refusal is the result an agent most has to act
    on; it is the last place a standing fact should be missing."""
    call = agent(_app(body={"notices": [STANDING]}, gateway=_refusing()))
    try:
        result = call("get_bot_status")
        text = _text(result)
        assert result.get("isError") is True, text
        assert "a_fixture_reason" in text, "the refusal itself is intact"
        assert f"{notices.PREFIX}{STANDING}" in text, "and the standing sentence rode along"
    finally:
        call.close()


def test_the_refusal_says_the_notice_once(agent):
    """ONCE: the field on the body line, then the trailing line. Not twice in either channel."""
    call = agent(_app(body={"notices": [STANDING]}, gateway=_refusing()))
    try:
        text = _text(call("get_bot_status"))
        assert text.count(STANDING) == 2, text
        assert text.count(f"{notices.PREFIX}{STANDING}") == 1, text
    finally:
        call.close()


def test_the_refusal_body_line_carries_the_field(agent):
    """BOTH CHANNELS on a refusal: `render_tool_error` puts the machine-readable body last, so a
    caller that parses a refusal finds the notices where it already looks."""
    call = agent(_app(body={"notices": [STANDING]}, gateway=_refusing()))
    try:
        block = _text(call("get_bot_status")).split(f"\n\n{notices.PREFIX}")[0]
        body = json.loads(block.splitlines()[-1])
        assert body["notices"] == [STANDING]
        assert body["reason"] == "a_fixture_reason", "the decider's own fields survive"
        assert body["action_url"] == REFUSAL["action_url"]
    finally:
        call.close()


def test_the_refusals_own_lines_are_untouched(agent):
    """A notice is ADDITIVE here too: the actionable first line and the `action_url:` line are byte
    for byte what `tool_errors` rendered."""
    plain = agent(_app(body={"notices": []}, gateway=_refusing()))
    withit = agent(_app(body={"notices": [STANDING]}, gateway=_refusing()))
    try:
        bare = _text(plain("get_bot_status")).splitlines()
        rich = _text(withit("get_bot_status")).splitlines()
        assert rich[:2] == bare[:2], (bare, rich)
    finally:
        plain.close()
        withit.close()


@pytest.mark.parametrize("kwargs", [
    {"flows_url": ""},                       # this deployment carries no flows domain
    {"delay": True},                         # the domain is there and slow
    {"status": 500},                         # the domain answered badly
    {"body": {}},                            # no field at all
    {"body": {"notices": "not a list"}},     # the answer is not the shape this expects
])
def test_a_refusal_with_no_notices_reaches_the_agent_exactly_as_it_was(agent, kwargs):
    """NEVER AN ERROR, on the raised path: whatever happens on the notices hop, the refusal the
    agent must act on is the refusal — unchanged, still marked an error."""
    reference = agent(_app(body={"notices": []}, gateway=_refusing()))
    try:
        expected = _text(reference("get_bot_status"))
    finally:
        reference.close()

    call = agent(_app(gateway=_refusing(), **kwargs))
    try:
        result = call("get_bot_status")
        assert result.get("isError") is True
        assert _text(result) == expected
        assert notices.PREFIX not in _text(result)
    finally:
        call.close()


def test_a_refusal_with_a_body_the_renderer_could_not_parse_keeps_its_shape(agent):
    """No JSON body means nowhere to put the field — so the trailing line carries it alone, exactly
    as `render` does for an array-shaped success."""
    def gateway(request):
        return httpx.Response(502, content=b"<html>Bad Gateway</html>")

    call = agent(_app(body={"notices": [STANDING]}, gateway=gateway))
    try:
        text = _text(call("get_bot_status"))
        assert text.startswith("HTTP 502\n<html>Bad Gateway</html>"), text
        assert text.endswith(f"{notices.PREFIX}{STANDING}")
        assert text.count(STANDING) == 1, "one channel, because there is only one"
    finally:
        call.close()


# ── S5 · the surface is unchanged ─────────────────────────────────────────────────────────────

def test_no_new_tool_and_no_new_argument():
    """A mechanism that added a verb would be a verb an agent has to know to call, which is the
    thing this exists to avoid."""
    from test_mcp_surface import EXPECTED_TOOLS

    app = _app(body={"notices": [STANDING]})
    tools = {t.name: t for t in app.state.mcp.tools}
    assert set(tools) == EXPECTED_TOOLS
    for name in notices.CARRIES_NOTICES:
        schema = tools[name].inputSchema
        assert notices.FIELD not in (schema.get("properties") or {})


# ── the pure parts, driven directly ───────────────────────────────────────────────────────────

def test_render_leaves_a_non_object_body_shaped_exactly_as_it_was():
    """`list_meetings` answers with an ARRAY. A field has nowhere to go there, and inventing a
    wrapper for it would change what every existing caller parses — so the trailing line carries it
    and the body is untouched."""
    got = notices.render('[\n  {"id": 7}\n]', [STANDING])
    assert got.startswith('[\n  {"id": 7}\n]')
    assert got.endswith(f"{notices.PREFIX}{STANDING}")
    assert json.loads(got.split(f"\n\n{notices.PREFIX}")[0]) == [{"id": 7}]


def test_render_leaves_a_body_that_already_carries_the_field_alone():
    body = json.dumps({"notices": ["already here"]})
    assert json.loads(notices.render(body, [STANDING]).split(f"\n\n{notices.PREFIX}")[0]) == {
        "notices": ["already here"]}


def test_render_with_no_notices_changes_nothing():
    assert notices.render('{"a": 1}', []) == '{"a": 1}'


def test_render_error_with_no_notices_changes_nothing():
    block = render_tool_error(403, json.dumps({"detail": REFUSAL}))
    assert notices.render_error(block, []) == block


def test_render_error_puts_the_field_on_the_body_line_and_the_lines_after_the_block():
    block = render_tool_error(403, json.dumps({"detail": REFUSAL}))
    got = notices.render_error(block, [STANDING, SECOND])
    head, *tail = got.split("\n\n")
    assert head.splitlines()[:2] == block.splitlines()[:2], "the words the decider authored"
    assert json.loads(head.splitlines()[-1]) == {**REFUSAL, "notices": [STANDING, SECOND]}
    assert tail == [f"{notices.PREFIX}{STANDING}", f"{notices.PREFIX}{SECOND}"]


def test_render_error_leaves_a_block_with_no_json_body_shaped_exactly_as_it_was():
    block = render_tool_error(502, "<html>Bad Gateway</html>")
    got = notices.render_error(block, [STANDING])
    assert got == f"{block}\n\n{notices.PREFIX}{STANDING}"


def test_render_error_leaves_a_body_that_already_carries_the_field_alone():
    block = render_tool_error(403, json.dumps({"detail": {**REFUSAL, "notices": ["already here"]}}))
    body = json.loads(notices.render_error(block, [STANDING]).split("\n\n")[0].splitlines()[-1])
    assert body["notices"] == ["already here"]


def test_render_error_on_a_status_rendered_alone_still_carries_the_line():
    assert notices.render_error(render_tool_error(500, ""), [STANDING]) == (
        f"HTTP 500\n\n{notices.PREFIX}{STANDING}")


def test_clean_dedupes_in_order_and_drops_everything_that_is_not_a_sentence():
    assert notices.clean([STANDING, "", SECOND, STANDING, None, 7, {"x": 1}]) == [STANDING, SECOND]


@pytest.mark.parametrize("value", [None, "a string", {"a": 1}, 7])
def test_clean_never_raises_on_a_shape_it_did_not_expect(value):
    assert notices.clean(value) == []


@pytest.mark.parametrize("headers,expected", [
    ({"x-api-key": "k"}, "k"),
    ({"Authorization": "Bearer k"}, "k"),
    ({"AUTHORIZATION": "Bearer k"}, "k"),
    ({"x-api-key": "k", "authorization": "Bearer other"}, "k"),
    ({}, ""),
    (None, ""),
])
def test_the_caller_key_is_read_the_way_every_other_boundary_reads_it(headers, expected):
    assert notices.caller_key(headers) == expected
