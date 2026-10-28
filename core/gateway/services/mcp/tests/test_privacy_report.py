"""D6: a tool this edge calls DIRECTLY (no gateway forward) reports its privacy label to the gateway.

* the domain receives the session's current mark as ``X-Vexa-Taint`` (asked of the gateway);
* a private or unlabelled answer is reported on ``/internal/mcp/taint`` with the signed re-entry;
* an answer whose report fails is withheld (503 ``privacy_mark_unavailable``);
* a ``none`` answer reports nothing.
"""
from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vexa_mcp import bind, register
from vexa_mcp import manifest as m
from vexa_mcp import reentry
from test_register import FLOWS_OPENAPI, MANIFEST

GW = "http://gateway:8000"


def _app(label, gateway_ok=True, state="mail"):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url).startswith(GW + "/internal/mcp/taint"):
            if not gateway_ok:
                return httpx.Response(503, json={})
            body = json.loads(request.content or b"{}")
            return httpx.Response(200, json={"marked": bool(body.get("sources")), "state": state})
        headers = {"x-vexa-privacy": label} if label is not None else {}
        return httpx.Response(200, json={"rows": ["Acme owes 615"]}, headers=headers)

    app = FastAPI()
    assembly = m.assemble([MANIFEST], deployed={"identity", "flows"})
    bound = bind.verify(assembly, {"flows": FLOWS_OPENAPI})
    register.register(app, bound, {"flows": "http://flows"}, transport=httpx.MockTransport(handler),
                      gateway_url=GW)
    app.add_middleware(reentry.ReentryMiddleware)
    return TestClient(app), seen


HDR = {"X-API-Key": "vxd_worker", "x-vexa-identity": "signed-by-the-gateway"}


def test_a_private_direct_answer_is_reported_and_the_mark_rides_the_call():
    client, seen = _app("private; class=crm")
    r = client.get("/tools/flows_list", headers=HDR)
    assert r.status_code == 200, r.text
    domain = next(x for x in seen if str(x.url).startswith("http://flows"))
    assert domain.headers["x-vexa-taint"] == "mail"
    reports = [json.loads(x.content) for x in seen if "/internal/mcp/taint" in str(x.url)]
    assert reports == [{"sources": []}, {"sources": ["crm"]}]
    report = [x for x in seen if "/internal/mcp/taint" in str(x.url)][-1]
    assert report.headers["x-vexa-internal-mcp-identity"] == "signed-by-the-gateway"


def test_an_unlabelled_direct_answer_is_private():
    client, seen = _app(None)
    client.get("/tools/flows_list", headers=HDR)
    assert json.loads([x for x in seen if "/internal/mcp/taint" in str(x.url)][-1].content) == {
        "sources": ["unlabelled"]}


def test_an_answer_whose_report_fails_is_withheld():
    client, _ = _app("private; class=crm", gateway_ok=False)
    r = client.get("/tools/flows_list", headers=HDR)
    assert r.status_code == 503
    assert "Acme" not in r.text and r.json()["detail"]["refused"] == "privacy_mark_unavailable"


def test_a_none_answer_reports_nothing():
    client, seen = _app("none")
    assert client.get("/tools/flows_list", headers=HDR).status_code == 200
    assert [json.loads(x.content) for x in seen if "/internal/mcp/taint" in str(x.url)] == [{"sources": []}]
