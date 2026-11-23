"""Incoming webhooks: a person's hook takes signed events from Stripe or Vexa's meeting service on a
public path, keeps only what verifies, and the person's sensor pages them. Signatures here are real
HMACs computed the way each sender computes them; nothing reaches a network."""
import hashlib
import hmac
import json
import time
from contextlib import closing

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


def test_the_sensors_role_neither_creates_nor_revokes(signed, hook, store):
    assert signed("sensors", "POST", "/api/hooks", {"label": "x", "scheme": "vexa"}, actor=OWNER).status_code == 403
    h = hook("vexa")
    assert signed("sensors", "POST", f"/api/hooks/{h['hook_id']}/revoke", actor=OWNER).status_code == 403
    assert "hooks/" + h["hook_id"] in store.rows


def test_another_person_cannot_revoke(signed, hook, store):
    h = hook("vexa")
    assert signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke", actor="someone-else").status_code == 404
    assert signed("agent", "POST", f"/api/hooks/{h['hook_id']}/revoke", actor="someone-else").status_code == 404
    assert "hooks/" + h["hook_id"] in store.rows


def test_an_unwatched_worker_may_revoke(signed, hook, store):
    h = hook("vexa")
    worker = identity_token.sign(GATEWAY_KEY, {"sub": OWNER, "scopes": ["bot", "tx"],
                                               "delegation": {"regime": "autonomous", "workspaces": "*"}})
    assert signed("agent", "POST", f"/api/hooks/{h['hook_id']}/revoke", identity=worker).status_code == 200
    assert "hooks/" + h["hook_id"] not in store.rows


@pytest.mark.parametrize("role", ["bots", "git", "signals", "sensors"])
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
    assert outcomes == ["stored"]          # a redelivery writes no audit row


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


# ── review fixes ──────────────────────────────────────────────────────────────────────────────
def _post(client, h, eid, pad=0):
    raw = json.dumps({"event_id": eid, "pad": "x" * pad}).encode()
    r = client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    assert r.status_code == 200, r.text
    return raw


def test_a_page_is_bounded_by_bytes_and_says_has_more(client, signed, hook):
    h = hook("vexa")
    for i in range(100):
        _post(client, h, f"evt_{i}", pad=20000)
    first = events(signed, h["hook_id"])
    assert first.status_code == 200 and len(first.content) < 1024 * 1024
    page = first.json()
    assert 0 < len(page["items"]) < 100 and page["has_more"] is True
    size = sum(len(json.dumps(i["event"], ensure_ascii=False, separators=(",", ":")).encode()) for i in page["items"])
    assert size >= routes_hooks.PAGE_BYTES and size - 20100 < routes_hooks.PAGE_BYTES
    seen, cursor = [i["event_id"] for i in page["items"]], page["cursor"]
    while page["has_more"]:
        page = events(signed, h["hook_id"], cursor).json()
        seen += [i["event_id"] for i in page["items"]]
        cursor = page["cursor"]
    assert seen == [f"evt_{i}" for i in range(100)]
    assert events(signed, h["hook_id"], cursor).json()["has_more"] is False


def test_one_event_over_the_byte_bound_is_still_answered(client, signed, hook, monkeypatch):
    monkeypatch.setattr(routes_hooks, "PAGE_BYTES", 10)
    h = hook("vexa")
    _post(client, h, "evt_a", pad=100)
    _post(client, h, "evt_b", pad=100)
    page = events(signed, h["hook_id"]).json()
    assert [i["event_id"] for i in page["items"]] == ["evt_a"] and page["has_more"] is True
    page = events(signed, h["hook_id"], page["cursor"]).json()
    assert [i["event_id"] for i in page["items"]] == ["evt_b"] and page["has_more"] is False


def test_a_persons_bytes_are_bounded_across_hooks_oldest_first(client, hook, broker, monkeypatch):
    monkeypatch.setattr(routes_hooks, "ACTOR_BYTES", 3500)
    one, two = hook("vexa"), hook("vexa")
    other = hook("vexa", actor="someone-else")
    _post(client, other, "evt_other", pad=1000)
    sizes = [len(_post(client, h, f"evt_{i}", pad=1000)) for i, h in enumerate([one, two, one, two])]
    kept = [r["event_id"] for r in broker.sql("SELECT event_id FROM hook_events ORDER BY seq", rows=True)]
    assert kept == ["evt_other", "evt_1", "evt_2", "evt_3"]
    mine = broker.sql("SELECT SUM(size) AS n FROM hook_events h JOIN hooks k ON k.id=h.hook WHERE k.actor=?",
                      (OWNER,), one=True)["n"]
    assert mine == sum(sizes[1:]) <= 3500


def test_old_receive_audit_rows_are_pruned(client, hook, broker):
    h = hook("vexa")
    _post(client, h, "evt_0")
    broker.sql("UPDATE audit SET at=? WHERE action='hooks.receive'", (time.time() - 15 * 86400,))
    _post(client, h, "evt_1")
    assert broker.sql("SELECT COUNT(*) AS n FROM audit WHERE action='hooks.receive'", one=True)["n"] == 1
    assert broker.sql("SELECT COUNT(*) AS n FROM audit WHERE action='hooks.create'", one=True)["n"] == 2


@pytest.mark.parametrize("suffix", ["%0a", "%0d", "%09"])
def test_an_encoded_control_character_path_is_not_exempt(client, hook, suffix):
    h = hook("vexa")
    raw = vexa_event()
    r = client.post(h["path"] + suffix, content=raw, headers=vexa_headers(h["secret"], raw))
    assert r.status_code == 401 and r.json() == {"detail": "Product identity refused"}


def test_no_assertion_opens_another_hooks_path(signed, hook):
    """Assertions are only valid for /api/ paths, so nothing under /hooks but the exempt form is reachable."""
    h = hook("vexa")
    assert signed("human", "POST", "/hooks/" + h["hook_id"] + "x", {"event_id": "evt"}).status_code == 401


def test_the_route_holds_its_own_path_and_id_without_the_middleware(broker, hook):
    """Behind the exemption, the route checks the raw scope path and the id itself."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    bare = FastAPI()
    bare.include_router(routes_hooks.build(broker))
    c = TestClient(bare)
    h = hook("vexa")
    raw = vexa_event()
    for path in (h["path"] + "%0a", "/hooks/hk_short", "/hooks/" + h["hook_id"].upper()):
        r = c.post(path, content=raw, headers=vexa_headers(h["secret"], raw))
        assert r.status_code == 404 and r.json() == {"detail": "No such hook"}, path
    assert c.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw)).status_code == 200


def test_a_revoke_during_a_receive_keeps_nothing(client, signed, hook, broker, monkeypatch):
    h = hook("vexa")
    real = routes_hooks.verify_vexa

    def verify_then_revoke(*args):
        real(*args)
        assert signed("human", "POST", f"/api/hooks/{h['hook_id']}/revoke").status_code == 200

    monkeypatch.setattr(routes_hooks, "verify_vexa", verify_then_revoke)
    raw = vexa_event()
    r = client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    assert r.status_code == 404
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 0


def _nested(n):
    return ('{"event_id":"evt_deep","x":' + "[" * (n - 1) + "]" * (n - 1) + "}").encode()


def test_nesting_deeper_than_64_is_400(client, hook):
    h = hook("vexa")
    ok, deep, huge = _nested(64), _nested(65), _nested(100000)
    assert client.post(h["path"], content=ok, headers=vexa_headers(h["secret"], ok)).status_code == 200
    assert client.post(h["path"], content=deep, headers=vexa_headers(h["secret"], deep)).status_code == 400
    assert client.post(h["path"], content=huge, headers=vexa_headers(h["secret"], huge)).status_code == 400


@pytest.mark.parametrize("ts", ["²", "1" * 13, "+1700000000", "-5", "17e8"])
def test_a_timestamp_must_be_ascii_digits(client, hook, ts):
    """Signed correctly over the odd timestamp: refused for its form, never a 500."""
    h = hook("vexa")
    s = hook("stripe", secret=STRIPE_SECRET)
    raw = vexa_event()
    wire = ts.encode("latin-1")        # the header bytes; the broker reads them as latin-1, i.e. `ts`
    sig = "sha256=" + hmac.new(h["secret"].encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    assert client.post(h["path"], content=raw, headers={"X-Webhook-Signature": sig, "X-Webhook-Timestamp": wire}).status_code == 401
    sraw = stripe_event()
    v1 = hmac.new(STRIPE_SECRET.encode(), f"{ts}.".encode() + sraw, hashlib.sha256).hexdigest()
    assert client.post(s["path"], content=sraw, headers={"Stripe-Signature": f"t={ts},v1={v1}".encode("latin-1")}).status_code == 401


def test_the_events_page_runs_on_an_index(broker):
    with closing(broker.db()) as c:
        plan = " ".join(str(r[-1]) for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT seq FROM hook_events WHERE hook=? AND seq>? ORDER BY seq LIMIT 101", ("h", 0)))
    assert "hook_events_by_hook" in plan and "TEMP B-TREE" not in plan


def test_a_given_secret_is_stored_stripped(client, signed, hook, store):
    h = hook("vexa", secret="  spaced-secret\n")
    assert store.rows["hooks/" + h["hook_id"]][-1]["value"] == "spaced-secret"
    raw = vexa_event()
    assert client.post(h["path"], content=raw, headers=vexa_headers("spaced-secret", raw)).status_code == 200
    assert signed("human", "POST", "/api/hooks", {"label": "x", "scheme": "stripe", "secret": "   "}).status_code == 422


def test_pruned_after_cursor_says_events_were_lost_unread(client, signed, hook, monkeypatch):
    h = hook("vexa")
    assert events(signed, h["hook_id"]).json()["pruned_after_cursor"] is False
    _post(client, h, "evt_0")
    read = events(signed, h["hook_id"]).json()
    monkeypatch.setattr(routes_hooks, "KEEP_EVENTS", 2)
    for i in range(1, 5):
        _post(client, h, f"evt_{i}")
    # evt_1 and evt_2 were pruned before the reader (at evt_0's cursor) saw them
    page = events(signed, h["hook_id"], read["cursor"]).json()
    assert [i["event_id"] for i in page["items"]] == ["evt_3", "evt_4"] and page["pruned_after_cursor"] is True
    # read on from there: nothing lost after this cursor
    assert events(signed, h["hook_id"], page["cursor"]).json()["pruned_after_cursor"] is False


def test_two_interleaved_hooks_do_not_report_a_gap(client, signed, hook):
    one, two = hook("vexa"), hook("vexa")
    for i in range(3):
        _post(client, one, f"one_{i}")
        _post(client, two, f"two_{i}")
    for h, prefix in ((one, "one"), (two, "two")):
        page = events(signed, h["hook_id"]).json()
        assert [i["event_id"] for i in page["items"]] == [f"{prefix}_{i}" for i in range(3)]
        assert page["pruned_after_cursor"] is False
        seqs = [i["seq"] for i in page["items"]]
        assert seqs[1] - seqs[0] == 2                 # seqs skip the other hook's, and that is no gap
        assert events(signed, h["hook_id"], seqs[0]).json()["pruned_after_cursor"] is False


def test_age_and_byte_pruning_mark_the_gap_too(client, signed, hook, broker, monkeypatch):
    h = hook("vexa")
    _post(client, h, "evt_old")
    broker.sql("UPDATE hook_events SET received=?", (time.time() - 15 * 86400,))
    _post(client, h, "evt_new")
    page = events(signed, h["hook_id"]).json()
    assert [i["event_id"] for i in page["items"]] == ["evt_new"] and page["pruned_after_cursor"] is True
    monkeypatch.setattr(routes_hooks, "ACTOR_BYTES", 1500)
    g = hook("vexa")
    _post(client, g, "big_0", pad=1000)
    _post(client, g, "big_1", pad=1000)
    page = events(signed, g["hook_id"]).json()
    assert [i["event_id"] for i in page["items"]] == ["big_1"] and page["pruned_after_cursor"] is True


def test_a_lone_surrogate_is_refused_before_storing(client, signed, hook, broker):
    h = hook("vexa")
    # meeting-api builds its body with json.dumps, which writes a lone surrogate as an escape
    raw = json.dumps({"event_id": "evt_surrogate", "data": {"note": "\ud800"}}).encode()
    assert b"\\ud800" in raw
    r = client.post(h["path"], content=raw, headers=vexa_headers(h["secret"], raw))
    assert r.status_code == 400
    assert broker.sql("SELECT COUNT(*) AS n FROM hook_events", one=True)["n"] == 0
    assert events(signed, h["hook_id"]).status_code == 200
    ok = json.dumps({"event_id": "evt_pair", "note": "\ud83d\ude00 é"}).encode()
    assert client.post(h["path"], content=ok, headers=vexa_headers(h["secret"], ok)).status_code == 200


def test_old_read_audit_rows_are_pruned(signed, hook, broker):
    h = hook("vexa")
    events(signed, h["hook_id"])
    broker.sql("UPDATE audit SET at=? WHERE action='hooks.events'", (time.time() - 15 * 86400,))
    events(signed, h["hook_id"])
    assert broker.sql("SELECT COUNT(*) AS n FROM audit WHERE action='hooks.events'", one=True)["n"] == 1
