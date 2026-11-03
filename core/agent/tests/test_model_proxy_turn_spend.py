"""The spend cap holds INSIDE a turn: the model proxy prices every call it relays.

A harness reports a turn's usage only when the turn ends (the claude CLI's ``result``), so the ledger
alone let one long turn spend up to its request ceiling past the cap. The proxy now reads the
provider's usage from each answer (Anthropic SSE/JSON, OpenAI chat SSE/JSON, the Responses API),
prices it with the ledger's own function and table, keeps it as the turn's unreported spend, and
checks ledger + unreported before every request. The worker's report replaces that figure.

Offline: the proxy's ASGI app over fakeredis, a mock provider, and the ledger's own meter.
"""
from __future__ import annotations

import json

import fakeredis
import httpx
import pytest
from starlette.testclient import TestClient

from control_plane import llm_budget as lb
from control_plane import model_prices
from control_plane import model_proxy as mp

SECRET = "model-proxy-secret-for-tests-0123456789"
KEY = "sk-ant-api03-REAL-DEPLOYMENT-KEY-never-in-a-worker"
MODEL = "claude-sonnet-5"           # the bundled snapshot: $2 in, $10 out, $0.2 cached, per 1M tokens


def _sse(*events) -> list[bytes]:
    """An SSE answer, cut at awkward places (mid-line) the way a network delivers it."""
    raw = "".join(f"event: {e.get('type', 'x')}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
    return [raw[i:i + 37] for i in range(0, len(raw), 37)]


def anthropic_stream(inp=2000, cached=30000, out=4000, model=MODEL) -> list[bytes]:
    return _sse({"type": "message_start", "message": {"id": "msg_1", "model": model, "usage": {
                    "input_tokens": inp, "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached, "output_tokens": 1}}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                 "usage": {"output_tokens": out}},
                {"type": "message_stop"})


#: what one `anthropic_stream()` call costs at the snapshot's price
CALL_USD = (2000 * 2.0 + 30000 * 0.2 + 4000 * 10.0) / 1e6          # 0.05


class Provider:
    """Answers each request with the next scripted answer (SSE chunks, or a JSON body)."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.status = 200
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        a = self.answer
        if isinstance(a, list):
            async def body():
                for c in a:
                    yield c
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())
        return httpx.Response(self.status, json=a)


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv(model_prices.URL_ENV, "off")         # the bundled snapshot, never the network
    r = fakeredis.FakeRedis(decode_responses=True)
    grants = mp.Grants(r, SECRET)
    provider = Provider(anthropic_stream())
    with TestClient(mp.create_app(grants, transport=httpx.MockTransport(provider))) as client:
        yield r, grants, provider, client


def _route(**kw) -> mp.Route:
    base = dict(base_url="https://api.anthropic.com", auth=mp.AUTH_X_API_KEY, credential=KEY)
    base.update(kw)
    return mp.Route(**base)


def _token(grants, payer="u_jane", unit="agent-1-chat-main", caps=None, route=None) -> str:
    caps = caps if caps is not None else {("user", "day"): 0.10, ("org", "day"): 25.0}
    rid = grants.put_route(unit, (route or _route()).with_spend(payer, caps))
    return grants.mint(unit, rid, ttl_sec=900)


def _call(client, token, *, path="/llm/v1/messages", body=None):
    body = body or {"model": MODEL, "max_tokens": 8000, "stream": True,
                    "messages": [{"role": "user", "content": "work"}]}
    return client.post(path, headers={"x-api-key": token, "anthropic-version": "2023-06-01"}, json=body)


def _ledger(r, payer, scope="user"):
    day = lb._periods(__import__("time").time())["day"][0]
    return float(r.get(lb._key(scope, payer, "day", day)) or 0)


# ── the finding: one claude-code turn, streaming, never reporting ─────────────────────────────────

def test_a_turn_that_streams_without_reporting_is_stopped_at_the_cap_mid_turn(proxy):
    """RED on the base: the ledger never moves inside the turn, so every call is relayed (up to the
    600-request ceiling). GREEN: two $0.05 calls reach the $0.10 cap and the third is refused."""
    r, grants, provider, client = proxy
    token = _token(grants)
    codes = []
    for _ in range(10):
        resp = _call(client, token)
        codes.append(resp.status_code)
        if resp.status_code != 200:
            break
        assert b"message_stop" in resp.content              # the answer is relayed whole
    assert codes == [200, 200, 402], codes
    assert "user day" in resp.json()["error"]["message"]
    assert len(provider.requests) == 2
    assert _ledger(r, "u_jane") == 0.0                        # the worker reported nothing
    assert lb.unreported(r, "u_jane")["user"] == pytest.approx(2 * CALL_USD)


def test_positive_control_a_turn_under_the_cap_runs_normally(proxy):
    r, grants, provider, client = proxy
    token = _token(grants, caps={("user", "day"): 5.0, ("org", "day"): 25.0})
    assert [_call(client, token).status_code for _ in range(5)] == [200] * 5
    assert len(provider.requests) == 5
    assert lb.unreported(r, "u_jane")["user"] == pytest.approx(5 * CALL_USD)


def test_another_payers_spend_is_unaffected(proxy):
    r, grants, provider, client = proxy
    jane = _token(grants, payer="u_jane", unit="agent-jane")
    bob = _token(grants, payer="u_bob", unit="agent-bob")
    while _call(client, jane).status_code == 200:
        pass
    assert _call(client, jane).status_code == 402
    assert lb.unreported(r, "u_bob")["user"] == 0.0
    assert [_call(client, bob).status_code for _ in range(2)] == [200, 200]
    # the deployment's own cap sees both: the org figure is the sum
    assert lb.unreported(r, "u_bob")["org"] == pytest.approx(4 * CALL_USD)


def test_the_org_cap_counts_every_payers_unreported_spend(proxy):
    r, grants, provider, client = proxy
    caps = {("user", "day"): 5.0, ("org", "day"): 0.10}
    jane = _token(grants, payer="u_jane", unit="agent-jane", caps=caps)
    bob = _token(grants, payer="u_bob", unit="agent-bob", caps=caps)
    assert _call(client, jane).status_code == 200 and _call(client, bob).status_code == 200
    refused = _call(client, jane)
    assert refused.status_code == 402 and "org day" in refused.json()["error"]["message"]


# ── the worker's report replaces the proxy's figure: never counted twice ──────────────────────────

def test_no_double_count_after_the_worker_reports(proxy):
    r, grants, provider, client = proxy
    unit = "agent-1-chat-main"
    token = _token(grants, unit=unit, caps={("user", "day"): 1.0, ("org", "day"): 25.0})
    budget = lb.Budget(r, lambda: {"llm_budget_user_daily_usd": 1.0}, prices=grants.prices())
    topic = lambda u: f"unit:{u}:out"  # noqa: E731
    meter = lb.UsageMeter(budget, r, output_topic=topic)
    meter.watch(unit, "u_jane")
    assert [_call(client, token).status_code for _ in range(2)] == [200, 200]
    assert lb.unreported(r, "u_jane")["user"] == pytest.approx(2 * CALL_USD)
    # the claude CLI's `result` at the turn's end: its own total, the same two calls
    reported = {"cost": 2 * CALL_USD, "prompt_tokens": 64000, "completion_tokens": 8000, "calls": 1}
    r.xadd(topic(unit), {"event": json.dumps({"type": "done", "usage": reported})})
    meter.poll_once()
    assert _ledger(r, "u_jane") == pytest.approx(2 * CALL_USD)
    assert lb.unreported(r, "u_jane")["user"] == pytest.approx(0.0)       # replaced, not added
    r.xadd(topic(unit), {"event": json.dumps({"type": "turn-complete"})})
    meter.poll_once()
    assert lb.unreported(r, "u_jane") == {"user": 0.0, "org": 0.0}
    # what the proxy checks is now the ledger alone: 0.10 of 1.00, the next call runs
    assert _call(client, token).status_code == 200


def test_turn_complete_clears_what_the_report_did_not_name(proxy):
    """The proxy's price and the provider's own figure differ a little; the residue never outlives
    the turn."""
    r, grants, provider, client = proxy
    unit = "agent-1-chat-main"
    token = _token(grants, unit=unit, caps={("user", "day"): 1.0, ("org", "day"): 25.0})
    budget = lb.Budget(r, lambda: {}, prices=grants.prices())
    meter = lb.UsageMeter(budget, r, output_topic=lambda u: f"o:{u}")
    meter.watch(unit, "u_jane")
    _call(client, token)
    r.xadd(f"o:{unit}", {"event": json.dumps({"type": "done", "usage": {"cost": 0.04}})})
    r.xadd(f"o:{unit}", {"event": json.dumps({"type": "turn-complete"})})
    meter.poll_once()
    assert lb.unreported(r, "u_jane")["user"] == 0.0 and _ledger(r, "u_jane") == pytest.approx(0.04)


def test_the_proxy_prices_a_call_exactly_as_the_ledger_does(proxy):
    r, grants, _provider, _client = proxy
    usage = mp.ledger_usage(MODEL, {"input_tokens": 2000, "cache_read_input_tokens": 30000,
                                    "output_tokens": 4000})
    turn = mp.Turn(unit="u1", payer="u_jane")
    budget = lb.Budget(fakeredis.FakeRedis(decode_responses=True), lambda: {}, prices=grants.prices())
    assert grants.charge(turn, usage) == pytest.approx(budget.record("u_jane", usage)) == pytest.approx(CALL_USD)


def test_an_unlisted_model_is_priced_by_the_ledgers_own_fallback(proxy):
    """No second policy: the operator's `llm_price_*` rates sealed at dispatch, else the table's base
    open-weight price — what `llm_budget.cost_usd` charges the same call."""
    r, grants, _provider, _client = proxy
    usage = {"model": "acme/private-model-9", "prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert grants.charge(mp.Turn(unit="u1", payer="a", rates=(1.0, 3.0)), usage) == pytest.approx(4.0)
    fallback = lb.cost_usd(usage, lb.Caps(caps={}, price_in=None, price_out=None), grants.prices())
    assert grants.charge(mp.Turn(unit="u2", payer="b"), usage) == pytest.approx(fallback) and fallback > 0


def test_a_later_turn_bills_its_own_payer_not_the_one_the_route_was_sealed_for(proxy, monkeypatch):
    """A room of two: the running worker's route was sealed for alice; bob's turn on it is his."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    r, grants, _provider, client = proxy
    caps = {("user", "day"): 5.0}
    env: dict = {}
    mp.confine(env, url="http://model-proxy:8102", grants=grants, unit="room-1", cold=True,
               spend=("alice", caps))
    env2: dict = {}
    bob = mp.confine(env2, url="http://model-proxy:8102", grants=grants, unit="room-1", cold=False,
                     spend=("bob", caps))
    assert _call(client, bob).status_code == 200
    assert lb.unreported(r, "bob")["user"] == pytest.approx(CALL_USD)
    assert lb.unreported(r, "alice")["user"] == 0.0


# ── the provider's usage, both dialects, streamed and not ─────────────────────────────────────────

def _tap(content_type: str, chunks) -> dict:
    tap = mp.UsageTap(content_type)
    for c in chunks:
        tap.feed(c)
    return tap.usage()


def test_anthropic_usage_streamed_and_json():
    streamed = _tap("text/event-stream; charset=utf-8", anthropic_stream())
    assert streamed == {"model": MODEL, "prompt_tokens": 32000, "completion_tokens": 4000,
                        "cache_read_tokens": 30000}
    body = {"id": "msg_1", "model": MODEL, "content": [], "usage": {
        "input_tokens": 2000, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 30000,
        "output_tokens": 4000}}
    raw = json.dumps(body).encode()
    assert _tap("application/json", [raw[:50], raw[50:]]) == {
        "model": MODEL, "prompt_tokens": 32500, "completion_tokens": 4000, "cache_read_tokens": 30000}


def test_openai_chat_usage_streamed_and_json():
    chunks = _sse({"id": "c1", "model": "gpt-5.5", "choices": [{"delta": {"content": "o"}}], "usage": None},
                  {"id": "c1", "model": "gpt-5.5", "choices": [], "usage": {
                      "prompt_tokens": 1200, "completion_tokens": 300,
                      "prompt_tokens_details": {"cached_tokens": 1000}, "cost": 0.0042}})
    chunks.append(b"data: [DONE]\n\n")
    assert _tap("text/event-stream", chunks) == {"model": "gpt-5.5", "prompt_tokens": 1200,
                                                 "completion_tokens": 300, "cache_read_tokens": 1000,
                                                 "cost": 0.0042}
    body = json.dumps({"id": "c1", "model": "gpt-5.5", "choices": [],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
    assert _tap("application/json", [body]) == {"model": "gpt-5.5", "prompt_tokens": 10,
                                                "completion_tokens": 5, "cache_read_tokens": 0}


def test_openai_responses_usage_streamed_and_json():
    chunks = _sse({"type": "response.created", "response": {"model": "gpt-5.5-codex", "usage": None}},
                  {"type": "response.output_text.delta", "delta": "x"},
                  {"type": "response.completed", "response": {"model": "gpt-5.5-codex", "usage": {
                      "input_tokens": 900, "output_tokens": 70,
                      "input_tokens_details": {"cached_tokens": 800}}}})
    assert _tap("text/event-stream", chunks) == {"model": "gpt-5.5-codex", "prompt_tokens": 900,
                                                 "completion_tokens": 70, "cache_read_tokens": 800}
    body = json.dumps({"object": "response", "model": "gpt-5.5-codex",
                       "usage": {"input_tokens": 9, "output_tokens": 7}}).encode()
    assert _tap("application/json", [body])["completion_tokens"] == 7


def test_a_chat_stream_is_asked_for_its_usage_and_an_anthropic_one_is_left_alone(proxy):
    r, grants, provider, client = proxy
    token = _token(grants, caps={("user", "day"): 5.0},
                   route=_route(base_url="https://openrouter.ai/api/v1", auth=mp.AUTH_BEARER))
    provider.answer = _sse({"model": "z-ai/glm-5.3", "choices": [],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    _call(client, token, path="/llm/chat/completions",
          body={"model": "z-ai/glm-5.3", "stream": True, "messages": []})
    assert json.loads(provider.requests[-1].content)["stream_options"] == {"include_usage": True}
    _call(client, token, path="/llm/chat/completions",
          body={"model": "z-ai/glm-5.3", "stream": True, "stream_options": None, "messages": []})
    assert json.loads(provider.requests[-1].content)["stream_options"] is None
    provider.answer = anthropic_stream()
    _call(client, token)
    assert "stream_options" not in json.loads(provider.requests[-1].content)


def test_an_answer_that_reports_no_usage_is_counted_at_its_allowance(proxy):
    r, grants, provider, client = proxy
    token = _token(grants, caps={("user", "day"): 5.0})
    provider.answer = {"id": "msg_1", "model": MODEL, "content": []}
    assert _call(client, token, body={"model": MODEL, "max_tokens": 1000, "messages": []}).status_code == 200
    # 1000 output tokens at $10/1M, plus the request's size as prompt
    assert lb.unreported(r, "u_jane")["user"] >= 1000 * 10.0 / 1e6


def test_a_refused_upstream_answer_costs_nothing(proxy):
    r, grants, provider, client = proxy
    token = _token(grants, caps={("user", "day"): 5.0})
    provider.status, provider.answer = 429, {"type": "error", "error": {"type": "rate_limit_error"}}
    assert _call(client, token).status_code == 429
    assert lb.unreported(r, "u_jane")["user"] == 0.0


def test_a_turn_that_reports_nothing_is_recorded_at_the_proxys_figure_once(proxy):
    """The Codex harness reports no usage: the turn's proxied spend is its record at turn-complete,
    and two meters watching the unit record it once."""
    r, grants, provider, client = proxy
    unit = "agent-codex"
    token = _token(grants, unit=unit, caps={("user", "day"): 1.0, ("org", "day"): 25.0})
    budget = lb.Budget(r, lambda: {}, prices=grants.prices())
    meters = [lb.UsageMeter(budget, r, output_topic=lambda u: f"o:{u}") for _ in range(2)]
    for m in meters:
        m.watch(unit, "u_jane")
    assert [_call(client, token).status_code for _ in range(2)] == [200, 200]
    r.xadd(f"o:{unit}", {"event": json.dumps({"type": "turn-complete"})})
    for m in meters:
        m.poll_once()
    assert _ledger(r, "u_jane") == pytest.approx(2 * CALL_USD)
    assert lb.unreported(r, "u_jane") == {"user": 0.0, "org": 0.0}
