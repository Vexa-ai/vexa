"""The model-route chain, offline, as it runs since H5/W1: the dispatch's env (``build_unit_env``),
the model proxy's ``confine`` (the route sealed, a per-turn token in its place), the runtime's merge,
then a harness on the worker's env sending through the proxy to a recorded provider.

Shared by the route tests (``test_model_route_isolation``, ``test_subject_endpoint_route``): their
assertions about WHICH endpoint and WHICH key a route carries now read the route the proxy holds
(:class:`Worker` itself), and their assertions about what reaches the wire read what the PROVIDER
receives behind the proxy — while every chain also proves the worker's own env holds none of it.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Iterable, Mapping

import fakeredis
import httpx
from starlette.testclient import TestClient

from control_plane import model_proxy as mp
from shared import model_grant as mg

SECRET = "model-proxy-chain-secret-0123456789abcdef"
PROXY = "http://model-proxy:8102"


class Worker(dict):
    """The route as agent-api resolved it — endpoint, key, models, dialect, as the model proxy will
    use them — with the worker's real env (``confined``) and the grant store beside it."""

    confined: dict
    grants: mp.Grants


#: Spec keys that are not part of the route but that route tests read (budgets, effort, the mark).
_ALSO = ("VEXA_AGENT_CONTEXT_TOKENS", "VEXA_AGENT_EFFORT", "VEXA_AGENT_MAX_OUTPUT_TOKENS",
         "VEXA_MODEL_ROUTE", "VEXA_MODEL_BASE_URL_ALLOW")


def route_view(spec: Mapping[str, str], source: Mapping[str, str]) -> dict:
    """The route's names as agent-api reads them: the dispatch's stamp, else agent-api's own env."""
    view = {n: (spec[n] if n in spec else source.get(n, "")) for n in mp.ROUTE_ENV
            if n in spec or source.get(n)}
    view.update({n: spec[n] for n in _ALSO if n in spec})
    return view


def worker(spec: Mapping[str, str], source: Mapping[str, str], *,
           merge: Callable[[dict], dict] = dict, secrets: Iterable[str] = ()) -> Worker:
    """Confine ``spec`` as the dispatch does, merge it as the runtime does (``merge``), and check
    that none of ``secrets`` is anywhere in what the worker holds."""
    grants = mp.Grants(fakeredis.FakeRedis(decode_responses=True), SECRET)
    confined = dict(spec)
    mp.confine(confined, url=PROXY, grants=grants, unit="unit-1", cold=True, source=source)
    confined = merge(confined)
    held = json.dumps(confined)
    leaked = [s for s in secrets if s and s in held]
    assert not leaked, f"a model credential reached the worker's env: {len(leaked)} value(s)"
    w = Worker(route_view(spec, source))
    w.confined, w.grants = confined, grants
    return w


def turn(monkeypatch, tmp_path, w: Worker, isolate: Callable) -> list[dict]:
    """Run the openai-agent harness the way a worker does, on ``w.confined``, through the proxy; return
    what reached the provider."""
    from llm.openai_agent import OpenAIAgentHarness

    isolate(monkeypatch, w.confined)
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)
    monkeypatch.setattr(mg, "_revoke", lambda _token: None)
    sent: list[dict] = []

    def provider(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "auth": request.headers.get("Authorization"),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "ok"},
                                                      "finish_reason": "stop"}]})

    with TestClient(mp.create_app(w.grants, transport=httpx.MockTransport(provider))) as proxy:
        harness = OpenAIAgentHarness(transport=proxy._transport)
        assert harness.preflight() is None
        with mg.use(w.confined[mg.TOKEN_ENV]):
            events = list(harness.run_turn(tmp_path, "hi",
                                           model=os.environ.get("VEXA_AGENT_MODEL") or None))
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True, events[-1]
    assert sent, "nothing reached the provider"
    return sent
