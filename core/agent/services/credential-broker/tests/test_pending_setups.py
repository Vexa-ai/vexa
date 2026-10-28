"""A setup nobody finished does not pile up in Connections.

* A second request for the same service reuses or supersedes the earlier unfinished one.
* An unfinished setup (never saved, no OAuth application) left alone for 24 hours is hidden.
A connection with a stored credential or a saved OAuth application is never touched by either."""
import time

from credential_broker import routes_connections

ENDPOINT = "https://chat.service.test/api/v10/users/@me"


def listed(signed, role="agent"):
    return {c["id"]: c for c in signed(role, "GET", "/api/connections").json()["connections"]}


def prepare(signed, cid, endpoint=ENDPOINT, **extra):
    r = signed("agent", "POST", f"/api/connections/{cid}/prepare", {"setup": {"endpoint": endpoint, **extra}})
    assert r.status_code == 200, r.text


def test_same_label_setup_reuses_the_pending_row(signed, connection):
    first = connection("custom_secret", "Chat bot")
    assert connection("custom_secret", "chat bot ") == first
    assert list(listed(signed)) == [first]


def test_prepare_supersedes_a_pending_row_for_the_same_host(signed, connection):
    first = connection("custom_secret", "Chat")
    prepare(signed, first)
    second = connection("custom_secret", "Chat bot")
    prepare(signed, second, ENDPOINT.replace("users/@me", "channels/1/messages"), method="POST")
    rows = listed(signed)
    assert second in rows and first not in rows


def test_prepare_supersedes_an_unprepared_row_from_the_same_session(signed, connection):
    abandoned = connection("custom_secret", "Chat")  # prepare refused, never retried on this row
    other_session = signed("agent", "POST", "/api/setup", {"provider": "custom_secret", "label": "Other"},
                           session="another-session").json()["connection_id"]
    fresh = connection("custom_secret", "Chat bot")
    prepare(signed, fresh)
    rows = listed(signed)
    assert fresh in rows and abandoned not in rows and other_session in rows


def test_saved_connections_are_never_superseded(signed, connection, ready, broker):
    saved = connection("custom_secret", "Chat")
    prepare(signed, saved)
    ready(saved)
    oauth = connection("custom_secret", "Chat oauth")
    prepare(signed, oauth)
    broker.sql("UPDATE connections SET oauth_app_version=1 WHERE id=?", (oauth,))
    fresh = connection("custom_secret", "Chat bot")
    prepare(signed, fresh)
    assert {saved, oauth, fresh} <= set(listed(signed))
    # A new request for a label that is ready reuses nothing pending and keeps the ready one.
    assert connection("custom_secret", "Chat") != saved
    assert saved in listed(signed)


def test_other_people_are_untouched(signed, connection):
    theirs = connection("custom_secret", "Chat", actor="someone-else")
    prepare_theirs = signed("agent", "POST", f"/api/connections/{theirs}/prepare", {"setup": {"endpoint": ENDPOINT}},
                            actor="someone-else")
    assert prepare_theirs.status_code == 200
    mine = connection("custom_secret", "Chat")
    prepare(signed, mine)
    assert theirs != mine
    assert theirs in {c["id"] for c in signed("agent", "GET", "/api/connections", actor="someone-else").json()["connections"]}


def test_pending_setup_expires_after_ttl(signed, connection, ready, broker):
    stale = connection("custom_secret", "Old")
    kept = connection("custom_secret", "Kept")
    ready(kept)
    old = time.time() - routes_connections.PENDING_SETUP_TTL - 60
    broker.sql("UPDATE connections SET created=?, touched=? WHERE id IN (?,?)", (old, old, stale, kept))
    rows = listed(signed)
    assert stale not in rows and kept in rows
    assert stale not in listed(signed, "human")
    # Asking again brings a fresh one, not the stale row.
    again = connection("custom_secret", "Old")
    assert again != stale and again in listed(signed)


def test_a_re_request_keeps_a_pending_setup_alive(signed, connection, broker):
    cid = connection("custom_secret", "Chat")
    old = time.time() - routes_connections.PENDING_SETUP_TTL + 120
    broker.sql("UPDATE connections SET created=?, touched=? WHERE id=?", (old, old, cid))
    assert signed("agent", "POST", f"/api/connections/{cid}/request").status_code == 200
    assert broker.sql("SELECT touched FROM connections WHERE id=?", (cid,), one=True)["touched"] > time.time() - 60
