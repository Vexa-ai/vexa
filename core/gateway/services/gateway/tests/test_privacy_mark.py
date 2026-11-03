"""D6: the edge marks a worker's session from the privacy label on what it read, and fails closed.

* a private (or unlabelled) answer to a worker's request marks the unit and its conversation in Redis;
* a ``none`` answer marks nothing; a person's own key marks nothing;
* a private answer whose mark cannot be written is REFUSED (503 ``privacy_mark_unavailable``);
* the session's current mark rides the forward as ``X-Vexa-Taint`` (``unverifiable`` when unreadable),
  and a client cannot supply its own;
* the MCP reports a direct read on ``/internal/mcp/taint`` only with its signed re-entry, and the unit
  is the one this edge resolved.
"""
import time

from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from gateway import taint as taint_mod
from conftest import SIGNING_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

DELEGATED = "vxd_header.payload.signature"
UNIT = "agent-42-chat-main-g2"
USER = {"user_id": 42, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "ada@example.com",
        "delegation": {"regime": "human", "workspaces": ["ws_1"], "unit": UNIT}}


class FakeHashRedis:
    """The slice of redis.asyncio the marker uses."""

    def __init__(self, broken=False):
        self.h: dict = {}
        self.ttl: dict = {}
        self.broken = broken

    def pipeline(self, transaction=True):
        outer = self

        class _P:
            def __init__(self):
                self.ops = []

            def hset(self, key, mapping):
                self.ops.append(("hset", key, mapping))

            def expire(self, key, ttl):
                self.ops.append(("expire", key, ttl))

            async def execute(self):
                if outer.broken:
                    raise ConnectionError("down")
                for op, key, arg in self.ops:
                    if op == "hset":
                        outer.h.setdefault(key, {}).update(arg)
                    else:
                        outer.ttl[key] = arg
        return _P()

    async def hgetall(self, key):
        if self.broken:
            raise ConnectionError("down")
        return dict(self.h.get(key, {}))


def _client(label, store, status=200):
    extra = {taint_mod.LABEL_HEADER: label} if label is not None else {}
    downstream = FakeDownstream(status_code=status, extra_headers=extra)
    app = create_app(FakeAuthorizer(user=USER, valid_key=DELEGATED), downstream, FakeRedis(),
                     identity_key=SIGNING_KEY,
                     taint_marker=taint_mod.TaintMarker(store) if store is not None else None)
    return TestClient(app), downstream


def _reentry():
    return {"X-API-Key": DELEGATED,
            "X-Vexa-Internal-Mcp-Identity": identity_token.signed_headers(SIGNING_KEY, USER)[identity_token.HEADER]}


def test_a_private_answer_marks_the_unit_and_its_conversation():
    store = FakeHashRedis()
    client, _ = _client("private; class=transcript", store)
    r = client.get("/transcripts/search", params={"q": "x"}, headers=_reentry())
    assert r.status_code == 200, r.text
    assert set(store.h[f"vexa:taint:unit:{UNIT}"]) == {"transcript"}
    assert set(store.h["vexa:taint:session:agent-42-chat-main"]) == {"transcript"}
    assert store.ttl[f"vexa:taint:unit:{UNIT}"] == taint_mod.TTL_SEC


def test_an_unlabelled_answer_is_private():
    store = FakeHashRedis()
    client, _ = _client(None, store)
    assert client.get("/meetings", headers=_reentry()).status_code == 200
    assert set(store.h[f"vexa:taint:unit:{UNIT}"]) == {"unlabelled"}


def test_a_none_answer_marks_nothing():
    store = FakeHashRedis()
    client, _ = _client("none", store)
    assert client.get("/meetings", headers=_reentry()).status_code == 200
    assert store.h == {}


def test_a_private_answer_whose_mark_cannot_be_written_is_refused():
    client, _ = _client("private; class=transcript", FakeHashRedis(broken=True))
    r = client.get("/meetings", headers=_reentry())
    assert r.status_code == 503
    assert r.json()["detail"]["refused"] == "privacy_mark_unavailable"
    # and with no store configured at all
    client, _ = _client("private; class=transcript", None)
    assert client.get("/meetings", headers=_reentry()).status_code == 503


def test_the_forward_carries_the_sessions_mark_and_a_client_cannot_forge_it():
    store = FakeHashRedis()
    store.h["vexa:taint:session:agent-42-chat-main"] = {"mail": str(int(time.time()))}
    client, downstream = _client("none", store)
    client.get("/meetings", headers={**_reentry(), "X-Vexa-Taint": "none"})
    assert downstream.last["headers"]["x-vexa-taint"] == "mail"
    client, downstream = _client("none", FakeHashRedis(broken=True))
    client.get("/meetings", headers={**_reentry(), "X-Vexa-Taint": "none"})
    assert downstream.last["headers"]["x-vexa-taint"] == "unverifiable"


def test_the_mcp_reports_a_direct_read_only_on_its_re_entry():
    store = FakeHashRedis()
    client, _ = _client("none", store)
    r = client.post("/internal/mcp/taint", json={"sources": ["crm"]}, headers=_reentry())
    assert r.status_code == 200 and r.json() == {"marked": True, "state": "crm"}
    assert set(store.h[f"vexa:taint:unit:{UNIT}"]) == {"crm"}
    # the worker's own token without the signed re-entry is refused
    r = client.post("/internal/mcp/taint", json={"sources": ["crm"]}, headers={"X-API-Key": DELEGATED})
    assert r.status_code == 403
    # a store that cannot be written refuses, so the MCP withholds the tool's answer
    client, _ = _client("none", FakeHashRedis(broken=True))
    assert client.post("/internal/mcp/taint", json={"sources": ["crm"]}, headers=_reentry()).status_code == 503


def test_label_grammar_matches_the_agent_side():
    assert taint_mod.parse_label("none") == frozenset()
    assert taint_mod.parse_label(None) == {"unlabelled"}
    assert taint_mod.parse_label("private; class=crm,mail") == {"crm", "mail"}
    assert taint_mod.session_of_unit("agent-42-chat-main-g2-auto") == "agent-42-chat-main"
    assert taint_mod.session_of_unit("agent-42-scheduled-0a1b") == "agent-42-scheduled-0a1b"
