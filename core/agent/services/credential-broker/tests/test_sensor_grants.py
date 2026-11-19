"""08S-b: the sensors key reads a calendar only under a grant its person issued.

One sensors key serves the whole deployment, so the key cannot decide whose calendar is read. The
person does, with a sensor grant issued through their own signed identity (agent role with the
gateway's X-Vexa-Identity in a watched, private turn; or the terminal's human role), scoped to that
person, one calendar connection and `calendar.read`, revocable and expiring. These tests hold:

* the sensors service, holding only person A's grant, is refused B's calendar;
* a revoked grant and an expired grant read nothing;
* the positive path reads;
* only the person can issue one — never the sensors key, a worker with nobody in the loop, or a turn
  other people read;
* the upgrade step gives a bounded grant only to calendars the broker's own audit shows a sensor
  was already reading.
"""
import time
from unittest.mock import patch

import pytest

from conftest import GATEWAY_KEY, OWNER
from credential_broker import identity_token, providers, sensor_grants
from credential_broker.broker import Broker

PATH = "/api/internal/sensor/calendar-changes"
GRANTS = "/api/sensor-grants"
OTHER = "person-b"
ANSWER = {"items": [], "cursor": "tok-2", "resync_required": False}


def _calendar(connection, ready, store, *, actor=OWNER):
    cid = connection(provider="google_calendar", actor=actor)
    ready(cid)
    store.put(cid, {"owner": actor, "value": {"access_token": "SECRET-" + actor,
                                              "expires_at": time.time() + 300}})
    return cid


def _read(signed, owner, **extra):
    with patch.object(providers, "calendar_changes", return_value=ANSWER) as read:
        r = signed("sensors", "POST", PATH, {"owner": owner, **extra}, actor=owner)
    return r, read


def _grant(signed, actor=OWNER, body=None, **kw):
    return signed("agent", "POST", GRANTS, body or {}, actor=actor, **kw)


def test_without_a_grant_the_sensors_key_reads_nobody(signed, connection, ready, store):
    _calendar(connection, ready, store)
    r, read = _read(signed, OWNER)
    assert r.status_code == 409 and r.json()["reason"] == "sensor_grant_required", r.text
    assert not read.called


def test_holding_only_A_s_grant_the_sensors_service_is_refused_B_s_calendar(signed, connection, ready, store,
                                                                           broker):
    a = _calendar(connection, ready, store)
    b = _calendar(connection, ready, store, actor=OTHER)
    assert _grant(signed).status_code == 200
    # B, signed for B: B never granted
    r, read = _read(signed, OTHER)
    assert r.status_code == 409 and r.json()["reason"] == "sensor_grant_required" and not read.called
    # B, naming B's connection explicitly: still no grant
    r, read = _read(signed, OTHER, connection_id=b)
    assert r.status_code == 409 and not read.called
    # A's connection, asked for B: not B's connection, so not even looked at
    r, read = _read(signed, OTHER, connection_id=a)
    assert r.status_code == 409 and r.json()["detail"] == "No ready calendar connection for this person"
    assert not read.called
    # signed for B, owner A: the actor binding still holds
    with patch.object(providers, "calendar_changes", return_value=ANSWER) as read:
        r = signed("sensors", "POST", PATH, {"owner": OWNER}, actor=OTHER)
    assert r.status_code == 403 and not read.called
    refused = broker.sql("SELECT COUNT(*) AS n FROM audit WHERE actor=? AND action='calendar.changes' "
                         "AND outcome='refused'", (OTHER,), one=True)["n"]
    assert refused == 2


def test_the_positive_path_reads_under_the_person_s_grant(signed, connection, ready, store):
    cid = _calendar(connection, ready, store)
    g = _grant(signed)
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["connection_id"] == cid and body["scope"] == "calendar.read" and body["status"] == "active"
    assert body["expires"] - body["created"] == pytest.approx(90 * 86400)
    r, read = _read(signed, OWNER)
    assert r.status_code == 200, r.text
    assert r.json() == {"connection_id": cid, **ANSWER}
    assert read.call_args.args[0]["access_token"] == "SECRET-" + OWNER


def test_a_revoked_grant_reads_nothing(signed, connection, ready, store):
    _calendar(connection, ready, store)
    gid = _grant(signed).json()["grant_id"]
    assert _read(signed, OWNER)[0].status_code == 200
    r = signed("agent", "POST", f"{GRANTS}/{gid}/revoke", None)
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    r, read = _read(signed, OWNER)
    assert r.status_code == 409 and r.json()["reason"] == "sensor_grant_required" and not read.called
    assert "revoked" in r.json()["detail"]


def test_an_expired_grant_reads_nothing(signed, connection, ready, store, broker):
    _calendar(connection, ready, store)
    gid = _grant(signed, body={"days": 1}).json()["grant_id"]
    broker.sql("UPDATE sensor_grants SET expires=? WHERE id=?", (time.time() - 1, gid))
    r, read = _read(signed, OWNER)
    assert r.status_code == 409 and "expired" in r.json()["detail"] and not read.called
    listed = signed("agent", "GET", GRANTS).json()["grants"]
    assert [g["status"] for g in listed] == ["expired"]


def test_a_grant_ends_with_its_connection(signed, connection, ready, store, broker):
    cid = _calendar(connection, ready, store)
    _grant(signed)
    broker.sql("UPDATE connections SET status='disconnected' WHERE id=?", (cid,))
    r, read = _read(signed, OWNER, connection_id=cid)
    assert r.status_code == 409 and not read.called


def test_only_the_person_issues_a_grant(signed, connection, ready, store):
    _calendar(connection, ready, store)
    # the sensors key cannot mint the grant it needs
    assert signed("sensors", "POST", GRANTS, {}, actor=OWNER).status_code == 403
    # nor can the other machine roles
    for role in ("git", "bots", "signals"):
        assert signed(role, "POST", GRANTS, {}, actor=OWNER).status_code in (401, 403), role
    # a worker with nobody in the loop, or a turn other people read, cannot either
    unwatched = identity_token.sign(GATEWAY_KEY, {"sub": OWNER, "scopes": ["bot", "tx"],
                                                  "delegation": {"regime": "autonomous", "workspaces": "*"}})
    assert _grant(signed, identity=unwatched).status_code == 403
    shared = identity_token.sign(GATEWAY_KEY, {"sub": OWNER, "scopes": ["bot", "tx"],
                                               "delegation": {"regime": "human", "workspaces": ["t"],
                                                              "shared": True}})
    assert _grant(signed, identity=shared).status_code == 403
    # an agent call without the gateway's signature over the person is not the person
    assert _grant(signed, identity=None).status_code == 401
    # the positive controls: the person's own agent, and the terminal for the signed-in person
    assert _grant(signed).status_code == 200
    assert signed("human", "POST", GRANTS, {}, actor=OWNER).status_code == 200


def test_a_person_cannot_grant_on_another_person_s_connection_or_revoke_their_grant(signed, connection,
                                                                                  ready, store):
    b = _calendar(connection, ready, store, actor=OTHER)
    _calendar(connection, ready, store)
    assert _grant(signed, body={"connection_id": b}).status_code == 409
    gid = _grant(signed, actor=OTHER).json()["grant_id"]
    assert signed("agent", "POST", f"{GRANTS}/{gid}/revoke", None).status_code == 404
    assert _read(signed, OTHER)[0].status_code == 200


def test_a_new_grant_replaces_the_live_one(signed, connection, ready, store):
    _calendar(connection, ready, store)
    first = _grant(signed).json()["grant_id"]
    second = _grant(signed).json()["grant_id"]
    listed = {g["grant_id"]: g["status"] for g in signed("agent", "GET", GRANTS).json()["grants"]}
    assert listed == {first: "revoked", second: "active"}


def test_upgrade_adopts_only_calendars_a_sensor_was_reading(settings, store, signed, connection, ready, broker):
    reading = _calendar(connection, ready, store)
    idle = _calendar(connection, ready, store, actor=OTHER)
    stale = _calendar(connection, ready, store, actor="person-c")
    now = time.time()
    rows = [(now - 3600, OWNER, reading, "success"), (now - 3600, OTHER, idle, "refused"),
            (now - 30 * 86400, "person-c", stale, "success")]
    for at, actor, cid, outcome in rows:
        broker.sql("INSERT INTO audit(at,actor,session,operation_id,connection,action,outcome,vault_request_id,"
                   "version,receipt) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (at, actor, "sensor:x", "", cid, "calendar.changes", outcome, "", 1, ""))
    # the state directory of a release before grants: no adoption marker yet
    broker.sql("DELETE FROM broker_meta WHERE key='sensor_grants_adopted'")
    upgraded = Broker(settings, store)
    grants = upgraded.sql("SELECT actor, connection, issued_via, expires, created FROM sensor_grants", rows=True)
    assert [(g["actor"], g["connection"], g["issued_via"]) for g in grants] == [(OWNER, reading, "upgrade")]
    assert grants[0]["expires"] - grants[0]["created"] == pytest.approx(sensor_grants.ADOPT_DAYS * 86400)
    # once: a later boot adopts nothing, whatever the audit says by then
    assert sensor_grants.adopt(upgraded) == 0
    assert _read(signed, OWNER)[0].status_code == 200
    assert _read(signed, OTHER)[0].status_code == 409
