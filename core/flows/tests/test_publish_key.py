"""Security review 19.1: a producer holds a PUBLISH key, never the operator key.

The crm outbox posts `crm.record.changed` to `POST /events`. With the operator key it could also
submit, pause and retire every company's flows. Its publish key (`VEXA_FLOWS_CRM_PUBLISH_KEY`, in
`X-Flows-Publisher-Key`) admits the CRM's own facts with envelopes sealed as producer `crm`, and
nothing else: another fact type, another producer's envelope and every other route refuse it.

OFFLINE: the same in-process flows-api and sqlite double as `test_d8_audience.py`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from test_d8_audience import D, ENVELOPE, EVENT, KEY, REFS, _body, _pem, _people_admitted, _seal, api, fa  # noqa: F401
from flows_integrations import fact_keys as fk

PUBLISH = "crm-publish-key-not-a-placeholder"
AS_CRM = {"X-Flows-Publisher-Key": PUBLISH}
CARRIERS = Path(__file__).resolve().parents[1] / "contracts" / "flows.v1" / "carriers.json"


@pytest.fixture
def crm_key(api, monkeypatch):
    monkeypatch.setattr(fa, "PUBLISH_KEYS", {"crm": PUBLISH})
    return api


def test_the_crm_publish_key_admits_the_crms_sealed_fact(crm_key):
    r = crm_key.post("/events", json=_body("pk-sealed"), headers=AS_CRM)
    assert r.status_code == 202, r.text[:300]
    assert r.json()["admitted_by"] == "producer:crm"
    assert _people_admitted() == {f"user:{D}/card-watch"}


def test_the_publish_key_admits_no_other_producers_fact(crm_key):
    body = {"event_type": "meeting.completed", "source_event_id": "pk-other",
            "refs": {"uid": D, "meeting_id": 1}}
    r = crm_key.post("/events", json=body, headers=AS_CRM)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_this_producers_fact"


def test_the_publish_key_verifies_only_its_own_producers_envelope(crm_key):
    """An envelope genuinely sealed by `agent` (its own key, its public half installed) is refused
    on the CRM's publish key and admitted on the operator's intake."""
    agent_key = _pem()
    (Path(os.environ["VEXA_FACT_VERIFY_KEYS_DIR"]) / "agent.pem").write_bytes(fk.public_pem(agent_key))
    as_agent = {**ENVELOPE, "producer": "agent"}
    body = {"event_type": EVENT, "source_event_id": "pk-agent-seal", "refs": REFS,
            "envelope": _seal(as_agent, fk.load_signer(agent_key), source_event_id="pk-agent-seal")}
    r = crm_key.post("/events", json=body, headers=AS_CRM)
    assert r.status_code == 403 and r.json()["detail"] == {"refused": "envelope_unverified"}
    # the operator's intake still verifies it: only the publish key is narrowed
    assert crm_key.post("/events", json=body, headers=KEY).status_code == 202


@pytest.mark.parametrize("method,path,body", [
    ("post", "/flows", {"name": "x", "on": "crm.record.changed", "steps": ["agent_run"]}),
    ("post", "/flows/card-watch/1/pause", None),
    ("post", "/events/batch", {"meetings": [{"url": "https://meet.google.com/abc-defg-hij",
                                             "organizer": "a@b.test", "start": 1.0}]}),
    ("get", "/reactions?subject=302", None),
    ("get", "/timeline?subject=302", None),
])
def test_the_publish_key_opens_no_other_route(crm_key, method, path, body):
    kw = {"headers": AS_CRM} | ({"json": body} if body is not None else {})
    assert getattr(crm_key, method)(path, **kw).status_code in (401, 403)


def test_a_wrong_or_absent_publish_key_is_401(crm_key):
    assert crm_key.post("/events", json=_body("pk-wrong"),
                        headers={"X-Flows-Publisher-Key": "nope"}).status_code == 401
    assert crm_key.post("/events", json=_body("pk-none")).status_code == 401


def test_unset_no_publish_key_opens_the_intake(api, monkeypatch):
    monkeypatch.setattr(fa, "PUBLISH_KEYS", {})
    assert api.post("/events", json=_body("pk-unset"),
                    headers={"X-Flows-Publisher-Key": ""}).status_code == 401


def test_a_placeholder_publish_key_refuses_boot(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_CRM_PUBLISH_KEY", "changeme")
    with pytest.raises(RuntimeError, match="VEXA_FLOWS_CRM_PUBLISH_KEY"):
        fa._publish_keys()


def test_each_publish_key_admits_exactly_the_facts_its_producer_owns():
    owned: dict = {}
    for c in json.loads(CARRIERS.read_text())["carriers"]:
        owned.setdefault(c["owner"], set()).add(c["event"])
    for producer, (_env, events) in fa.PUBLISHERS.items():
        assert set(events) == owned[producer], producer
