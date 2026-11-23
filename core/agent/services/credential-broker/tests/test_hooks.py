"""Incoming webhooks: a person's hook takes signed events from Stripe or Vexa's meeting service on a
public path, keeps only what verifies, and the person's sensor pages them. Signatures here are real
HMACs computed the way each sender computes them; nothing reaches a network."""
import hashlib
import hmac
import json
import time

import pytest

from credential_broker import identity_token, routes_hooks
from conftest import GATEWAY_KEY, OWNER

EVENTS = "/api/internal/sensor/hook-events"
STRIPE_SECRET = "whsec_fixture-stripe-signing-secret"


def stripe_header(secret, raw, t=None, extra=()):
    t = str(int(time.time()) if t is None else t)
    sig = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return ",".join([f"t={t}", *[f"v1={e}" for e in extra], f"v1={sig}"])


def vexa_headers(secret, raw, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    sig = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return {"X-Webhook-Signature": sig, "X-Webhook-Timestamp": ts}


def stripe_event(eid="evt_1Fixture"):
    return json.dumps({"id": eid, "object": "event", "type": "invoice.paid", "data": {"object": {"id": "in_1"}}}).encode()


def vexa_event(eid="evt_meeting_1"):
    return json.dumps({"event_id": eid, "event_type": "meeting.completed", "data": {"meeting": {"id": 7}}}).encode()


@pytest.fixture
def hook(signed):
    def make(scheme="vexa", secret=None, actor=OWNER, role="human"):
        body = {"label": "Fixture hook", "scheme": scheme}
        if secret is not None:
            body["secret"] = secret
        r = signed(role, "POST", "/api/hooks", body, actor=actor)
        assert r.status_code == 200, r.text
        return r.json()
    return make


def events(signed, hook_id, cursor=None, owner=OWNER, actor=OWNER, role="sensors"):
    body = {"owner": owner, "hook_id": hook_id}
    if cursor is not None:
        body["cursor"] = cursor
    return signed(role, "POST", EVENTS, body, actor=actor)


# ── create ────────────────────────────────────────────────────────────────────────────────────
def test_a_vexa_hook_without_a_secret_gets_one_answered_once(signed, hook, store, broker):
    h = hook("vexa")
    assert h["hook_id"].startswith("hk_") and len(h["hook_id"]) == 35
    assert h["path"] == "/hooks/" + h["hook_id"] and h["scheme"] == "vexa"
    assert len(h["secret"]) == 64 and int(h["secret"], 16) >= 0
    rec = store.rows["hooks/" + h["hook_id"]][-1]
    assert rec["value"] == h["secret"] and rec["owner"] == OWNER
    # never in metadata.sqlite
    with open(broker.db_path, "rb") as f:
        assert h["secret"].encode() not in f.read()
    # never answered again: not by revoke, not by the events read
    page = events(signed, h["hook_id"])
    assert h["secret"] not in page.text
    gone = signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke")
    assert gone.status_code == 200 and "secret" not in gone.json() and h["secret"] not in gone.text
    assert broker.sql("SELECT action, outcome FROM audit WHERE action='hooks.create'", rows=True) == [
        {"action": "hooks.create", "outcome": "requested"}, {"action": "hooks.create", "outcome": "stored"}]


def test_a_given_vexa_secret_is_stored_and_not_answered(hook, store):
    h = hook("vexa", secret="my-own-vexa-secret")
    assert "secret" not in h
    assert store.rows["hooks/" + h["hook_id"]][-1]["value"] == "my-own-vexa-secret"


def test_a_stripe_hook_needs_its_secret(signed, hook, store):
    h = hook("stripe", secret=STRIPE_SECRET)
    assert "secret" not in h and store.rows["hooks/" + h["hook_id"]][-1]["value"] == STRIPE_SECRET
    r = signed("human", "POST", "/api/hooks", {"label": "x", "scheme": "stripe"})
    assert r.status_code == 422


def test_the_agent_creates_too_but_not_an_unwatched_worker(signed, hook):
    assert hook("vexa", role="agent")["scheme"] == "vexa"
    worker = identity_token.sign(GATEWAY_KEY, {"sub": OWNER, "scopes": ["bot", "tx"],
                                               "delegation": {"regime": "autonomous", "workspaces": "*"}})
    r = signed("agent", "POST", "/api/hooks", {"label": "x", "scheme": "vexa"}, identity=worker)
    assert r.status_code == 403 and r.json()["detail"] == "This session runs without a person in the loop"


def test_the_sensors_role_creates_and_revokes_for_its_actor(signed, hook, store):
    h = hook("vexa", role="sensors", actor="42")
    assert store.rows["hooks/" + h["hook_id"]][-1]["owner"] == "42"
    # another person (any role) cannot revoke it
    assert signed("sensors", "POST", f"/api/hooks/{h['hook_id']}/revoke", actor="43").status_code == 404
    assert signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke").status_code == 404
    assert signed("sensors", "POST", f"/api/hooks/{h['hook_id']}/revoke", actor="42").status_code == 200
    assert "hooks/" + h["hook_id"] not in store.rows


@pytest.mark.parametrize("role", ["bots", "git", "signals"])
def test_other_roles_cannot_create(signed, role):
    r = signed(role, "POST", "/api/hooks", {"label": "x", "scheme": "vexa"})
    assert r.status_code == 403


def test_at_most_twenty_live_hooks_per_person(signed, hook):
    made = [hook("vexa") for _ in range(routes_hooks.MAX_HOOKS)]
    over = signed("human", "POST", "/api/hooks", {"label": "x", "scheme": "vexa"})
    assert over.status_code == 409 and over.json()["detail"] == "Too many hooks"
    # another person is not counted against
    assert hook("vexa", actor="someone-else")["scheme"] == "vexa"
    # a revoked hook frees its place
    assert signed("human", "POST", f"/api/hooks/{made[0]['hook_id']}/revoke").status_code == 200
    assert hook("vexa")["scheme"] == "vexa"


# ── receive ───────────────────────────────────────────────────────────────────────────────────
def test_stripe_happy_path_is_kept_and_read(client, signed, hook, broker):
    h = hook("stripe", secret=STRIPE_SECRET)
    raw = stripe_event()
    r = client.post(h["path"], content=raw, headers={"Stripe-Signature": stripe_header(STRIPE_SECRET, raw)})
    assert r.status_code == 200 and r.json() == {"received": True}
    page = events(signed, h["hook_id"]).json()
    assert page["scheme"] == "stripe" and page["items"][0]["event_id"] == "evt_1Fixture"
    assert page["items"][0]["event"] == json.loads(raw)


def test_vexa_happy_path_ignores_the_bearer_header(client, signed, hook):
    h = hook("vexa")
    raw = vexa_event()
    hdr = {**vexa_headers(h["secret"], raw), "Authorization": "Bearer " + h["secret"]}
    assert client.post(h["path"], content=raw, headers=hdr).status_code == 200
    item = events(signed, h["hook_id"]).json()["items"][0]
    assert item["event_id"] == "evt_meeting_1" and item["event"]["event_type"] == "meeting.completed"


def test_a_redelivery_is_a_200_and_one_row(client, hook, broker):
    h = hook("vexa")
    raw = vexa_event()
    for _ in range(3):
        assert client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw)).status_code == 200
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 1
    outcomes = [r["outcome"] for r in broker.sql("SELECT outcome FROM audit WHERE action='hooks.receive' ORDER BY seq", rows=True)]
    assert outcomes == ["stored", "duplicate", "duplicate"]


def test_a_wrong_signature_is_refused(client, hook, broker):
    h = hook("vexa")
    raw = vexa_event()
    r = client.post(h["path"], content=raw, headers=vexa_headers("not-the-secret", raw))
    assert r.status_code == 401 and r.json() == {"detail": "Signature refused"}
    s = hook("stripe", secret=STRIPE_SECRET)
    r = client.post(s["path"], content=stripe_event(), headers={"Stripe-Signature": stripe_header("whsec_other", stripe_event())})
    assert r.status_code == 401 and r.json() == {"detail": "Signature refused"}
    # a body changed after signing
    r = client.post(s["path"], content=stripe_event("evt_other"),
                    headers={"Stripe-Signature": stripe_header(STRIPE_SECRET, stripe_event())})
    assert r.status_code == 401
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 0


def test_a_rotated_stripe_secret_second_v1_matches(client, hook):
    h = hook("stripe", secret=STRIPE_SECRET)
    raw = stripe_event()
    header = stripe_header(STRIPE_SECRET, raw, extra=["ab" * 32])      # the old secret's v1 first
    assert client.post(h["path"], content=raw, headers={"Stripe-Signature": header}).status_code == 200


@pytest.mark.parametrize("skew", [-301, 301])
def test_a_stale_timestamp_is_refused_both_ways(client, hook, skew):
    raw_v, raw_s = vexa_event(), stripe_event()
    v = hook("vexa")
    s = hook("stripe", secret=STRIPE_SECRET)
    at = int(time.time()) + skew
    assert client.post(v["path"], content=raw_v, headers=vexa_headers(v["secret"], raw_v, at)).status_code == 401
    assert client.post(s["path"], content=raw_s,
                       headers={"Stripe-Signature": stripe_header(STRIPE_SECRET, raw_s, at)}).status_code == 401


def test_bearer_alone_is_refused(client, hook):
    h = hook("vexa")
    r = client.post(h["path"], content=vexa_event(), headers={"Authorization": "Bearer " + h["secret"]})
    assert r.status_code == 401 and r.json() == {"detail": "Signature refused"}


def test_missing_or_malformed_signatures_are_refused(client, hook):
    s = hook("stripe", secret=STRIPE_SECRET)
    for header in ("", "t=abc,v1=00", "t=123", "v1=00"):
        r = client.post(s["path"], content=stripe_event(), headers={"Stripe-Signature": header} if header else {})
        assert r.status_code == 401, header
    v = hook("vexa")
    raw = vexa_event()
    good = vexa_headers(v["secret"], raw)
    assert client.post(v["path"], content=raw, headers={**good, "X-Webhook-Signature": good["X-Webhook-Signature"][7:]}).status_code == 401
    assert client.post(v["path"], content=raw, headers={"X-Webhook-Signature": good["X-Webhook-Signature"]}).status_code == 401


def test_unknown_and_revoked_hooks_are_404(client, signed, hook):
    raw = vexa_event()
    r = client.post("/hooks/hk_" + "0" * 32, content=raw, headers=vexa_headers("x", raw))
    assert r.status_code == 404 and r.json() == {"detail": "No such hook"}
    h = hook("vexa")
    assert signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke").status_code == 200
    r = client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    assert r.status_code == 404 and r.json() == {"detail": "No such hook"}


@pytest.mark.parametrize("path", ["/hooks/hk_" + "0" * 31, "/hooks/hk_" + "A" * 32, "/hooks/hk_" + "0" * 33,
                                  "/hooks/" + "0" * 32, "/hooks", "/hooks/hk_" + "0" * 32 + "/x"])
def test_paths_outside_the_exempt_pattern_need_an_assertion(client, path):
    r = client.post(path, content=b"{}")
    assert r.status_code == 401 and r.json() == {"detail": "Product identity refused"}


def test_only_post_is_exempt(client):
    r = client.get("/hooks/hk_" + "0" * 32)
    assert r.status_code == 401 and r.json() == {"detail": "Product identity refused"}


def test_a_body_over_256_kib_is_413(client, hook, broker):
    h = hook("vexa")
    prefix = b'{"event_id":"evt_big","pad":"'
    raw = prefix + b"x" * (256 * 1024 + 1 - len(prefix) - 2) + b'"}'
    assert len(raw) == 256 * 1024 + 1
    assert client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw)).status_code == 413
    # without a declared length too (streamed)
    r = client.post(h["path"], content=iter([raw[:100000], raw[100000:]]), headers=vexa_headers(h["secret"], raw))
    assert r.status_code == 413
    # exactly the limit is fine
    ok = raw[:-3] + b'"}'
    assert len(ok) == 256 * 1024
    assert client.post(h["path"], content=ok, headers=vexa_headers(h["secret"], ok)).status_code == 200
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 1


@pytest.mark.parametrize("raw", [b"not json", b"[1,2]", b'{"event_type":"x"}', b'{"event_id":""}', b'{"event_id":5}',
                                 b"\xff\xfe"])
def test_a_body_that_is_not_an_event_is_400(client, hook, raw):
    h = hook("vexa")
    assert client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw)).status_code == 400


def test_events_are_pruned_by_age_and_count(client, hook, broker, monkeypatch):
    h = hook("vexa")
    monkeypatch.setattr(routes_hooks, "KEEP_EVENTS", 3)
    for i in range(5):
        raw = vexa_event(f"evt_{i}")
        assert client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw)).status_code == 200
    kept = [r["event_id"] for r in broker.sql("SELECT event_id FROM hook_events ORDER BY seq", rows=True)]
    assert kept == ["evt_2", "evt_3", "evt_4"]
    broker.sql("UPDATE hook_events SET received=? WHERE event_id='evt_2'", (time.time() - 15 * 86400,))
    raw = vexa_event("evt_5")
    client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    kept = [r["event_id"] for r in broker.sql("SELECT event_id FROM hook_events ORDER BY seq", rows=True)]
    assert kept == ["evt_3", "evt_4", "evt_5"]


# ── read ──────────────────────────────────────────────────────────────────────────────────────
def test_events_page_by_cursor(client, signed, hook, monkeypatch):
    monkeypatch.setattr(routes_hooks, "PAGE", 2)
    h = hook("vexa")
    for i in range(5):
        raw = vexa_event(f"evt_{i}")
        client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    seen, cursor = [], None
    while True:
        page = events(signed, h["hook_id"], cursor).json()
        if not page["items"]:
            assert page["cursor"] == cursor
            break
        assert len(page["items"]) <= 2
        seqs = [i["seq"] for i in page["items"]]
        assert seqs == sorted(seqs) and (cursor is None or seqs[0] > cursor) and page["cursor"] == seqs[-1]
        seen += [i["event_id"] for i in page["items"]]
        cursor = page["cursor"]
    assert seen == [f"evt_{i}" for i in range(5)]
    empty = events(signed, hook("vexa")["hook_id"]).json()
    assert empty["items"] == [] and empty["cursor"] == 0


def test_another_owner_cannot_read(signed, hook):
    h = hook("vexa")
    assert events(signed, h["hook_id"], owner=OWNER, actor="intruder").status_code == 403
    # signed for the intruder, naming the intruder: not their hook
    r = events(signed, h["hook_id"], owner="intruder", actor="intruder")
    assert r.status_code == 404 and r.json() == {"detail": "No such hook"}


@pytest.mark.parametrize("role", ["agent", "human", "git", "bots", "signals"])
def test_only_the_sensors_role_reads_events(signed, hook, role):
    h = hook("vexa")
    assert events(signed, h["hook_id"], role=role).status_code == 403


def test_a_revoked_hook_reads_404_and_its_events_and_secret_are_gone(client, signed, hook, store, broker):
    h = hook("vexa")
    raw = vexa_event()
    client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 1
    assert signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke").status_code == 200
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 0
    assert "hooks/" + h["hook_id"] not in store.rows
    assert broker.sql("SELECT revoked FROM hooks WHERE id=?", (h["hook_id"],), one=True)["revoked"] > 0
    assert events(signed, h["hook_id"]).status_code == 404
    assert signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke").status_code == 200   # idempotent


# ── nothing secret anywhere ───────────────────────────────────────────────────────────────────
def test_nothing_secret_in_any_answer_or_log_line(client, signed, hook, broker, capsys):
    v = hook("vexa")
    s = hook("stripe", secret=STRIPE_SECRET)
    marker = "BODY-MARKER-do-not-log"
    raw_v = json.dumps({"event_id": "evt_m", "note": marker}).encode()
    raw_s = json.dumps({"id": "evt_s", "note": marker}).encode()
    hv, hs = vexa_headers(v["secret"], raw_v), stripe_header(STRIPE_SECRET, raw_s)
    answers = [
        client.post(v["path"], content=raw_v, headers={**hv, "Authorization": "Bearer " + v["secret"]}),
        client.post(s["path"], content=raw_s, headers={"Stripe-Signature": hs}),
        client.post(v["path"], content=raw_v, headers=vexa_headers("wrong", raw_v)),
        client.post(s["path"], content=raw_s, headers={"Stripe-Signature": stripe_header(STRIPE_SECRET, raw_s, 1)}),
        client.post(v["path"], content=b"no json " + marker.encode(), headers=vexa_headers(v["secret"], b"no json " + marker.encode())),
        signed("human", "POST", f"/api/hooks/{s['hook_id']}/revoke"),
    ]
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert lines and any(l["event"] == "hook_refused" for l in lines)
    secret_sigs = [v["secret"], STRIPE_SECRET, hv["X-Webhook-Signature"][7:], hs.split("v1=")[-1]]
    for text in [out] + [a.text for a in answers]:
        for value in secret_sigs + [marker]:
            assert value not in text
    # no hook id in a route field either
    assert all(v["hook_id"] not in json.dumps(l.get("fields", {}).get("route", "")) for l in lines)
    audit = json.dumps(broker.sql("SELECT * FROM audit", rows=True))
    assert marker not in audit and v["secret"] not in audit and STRIPE_SECRET not in audit
