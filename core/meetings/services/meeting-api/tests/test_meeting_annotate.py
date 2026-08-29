"""POST /meetings/{platform}/{native_meeting_id}/annotate — the caller's OWN description.

`title` and arbitrary `metadata`, writable in ANY status. This is the surface that makes a Vexa
meeting joinable to everything else the caller knows: a CRM id, a ticket, tags, an agent's own
summary. It is REST-first — the MCP tool is a thin wrapper over exactly these routes, so anything
proven here holds for curl, the SDKs and any MCP client alike.

The split against PATCH /meetings/{platform}/{native} is by WHAT is written, not when. PATCH edits
the INSTRUCTIONS for a meeting (url, schedule, auto-join) and is refused once the FSM owns the row.
Annotations are read by nothing in the dispatch pipeline, so writing them can never re-arm or
re-route anything — and the moments a description is most worth writing (mid-meeting, and after it
ends) are exactly the ones PATCH answered 409 for.

Drives the collector ``create_app`` over the in-memory fake, OFFLINE (TestClient, no docker/DB).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
H = {"x-user-id": str(USER)}
PLAT, NID = "google_meet", "abc-defg-hij"


class _NullRedis:
    async def publish(self, channel, data):
        return None


def _client(status="active"):
    """Default status is `active` deliberately: the FSM-owned state is the one that matters."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NID, status=status)
    return TestClient(create_app(store, redis=_NullRedis())), store, mid


def _annotate(client, body, **params):
    return client.post(f"/meetings/{PLAT}/{NID}/annotate", json=body, headers=H, params=params)


# ---- the whole point: it works while the bot FSM owns the row ------------------------

def test_annotate_works_on_a_live_meeting():
    """PATCH answers 409 here. Annotating must not — a meeting is most worth describing while
    it is happening."""
    client, store, mid = _client(status="active")
    r = _annotate(client, {"title": "Acme renewal", "metadata": {"crm_deal": "acme-42"}})
    assert r.status_code == 200, r.text
    assert store._meetings[mid]["status"] == "active", "annotating must not touch status"


def test_patch_still_refuses_a_live_meeting():
    """The complement: dispatch parameters stay protected. This branch widens what can be
    written, not who owns the FSM."""
    client, _store, _mid = _client(status="active")
    r = client.patch(f"/meetings/{PLAT}/{NID}", json={"title": "nope"}, headers=H)
    assert r.status_code == 409


# ---- title round-trips (it lives in the data blob, not a column) ---------------------

def test_title_is_persisted_and_read_back():
    """Regression: the first implementation assigned `meeting.title`, which is not a column —
    SQLAlchemy accepted the attribute and persisted nothing, and a forwarding-only test could
    not see it. Assert the STORED value, not the request that was sent."""
    client, store, mid = _client()
    r = _annotate(client, {"title": "Fan vibration debug"})
    assert r.status_code == 200, r.text
    assert store._meetings[mid]["data"]["title"] == "Fan vibration debug"


def test_empty_title_clears_it():
    client, store, mid = _client()
    _annotate(client, {"title": "temporary"})
    _annotate(client, {"title": ""})
    assert "title" not in store._meetings[mid]["data"]


def test_title_is_capped_at_512_chars():
    client, store, mid = _client()
    _annotate(client, {"title": "x" * 900})
    assert len(store._meetings[mid]["data"]["title"]) == 512


# ---- metadata merge semantics --------------------------------------------------------

def test_metadata_merges_key_wise():
    client, store, mid = _client()
    _annotate(client, {"metadata": {"crm_deal": "acme-42", "owner": "dmitry"}})
    _annotate(client, {"metadata": {"stage": "discovery"}})
    assert store._meetings[mid]["data"]["metadata"] == {
        "crm_deal": "acme-42", "owner": "dmitry", "stage": "discovery",
    }


def test_explicit_null_deletes_one_key():
    """The only way to take back a single annotation without replacing the whole object."""
    client, store, mid = _client()
    _annotate(client, {"metadata": {"crm_deal": "acme-42", "owner": "dmitry"}})
    _annotate(client, {"metadata": {"owner": None}})
    assert store._meetings[mid]["data"]["metadata"] == {"crm_deal": "acme-42"}


def test_replace_swaps_the_whole_object():
    client, store, mid = _client()
    _annotate(client, {"metadata": {"crm_deal": "acme-42", "owner": "dmitry"}})
    _annotate(client, {"metadata": {"only": "this"}}, replace="true")
    assert store._meetings[mid]["data"]["metadata"] == {"only": "this"}


def test_title_and_metadata_are_independent():
    """Writing one must not clear the other — they share the same data blob."""
    client, store, mid = _client()
    _annotate(client, {"title": "Acme renewal"})
    _annotate(client, {"metadata": {"crm_deal": "acme-42"}})
    data = store._meetings[mid]["data"]
    assert data["title"] == "Acme renewal"
    assert data["metadata"] == {"crm_deal": "acme-42"}


# ---- refusals ------------------------------------------------------------------------

def test_empty_body_is_refused():
    client, _store, _mid = _client()
    r = _annotate(client, {})
    assert r.status_code == 422
    assert "title" in r.text and "metadata" in r.text


def test_non_object_metadata_is_refused():
    client, _store, _mid = _client()
    assert _annotate(client, {"metadata": ["not", "an", "object"]}).status_code == 422


def test_unknown_meeting_is_404():
    client, _store, _mid = _client()
    r = client.post(
        f"/meetings/{PLAT}/no-such-meeting/annotate", json={"title": "x"}, headers=H,
    )
    assert r.status_code == 404


def test_another_users_meeting_is_404_not_403():
    """Owner-scoped, and it must not confirm the row exists to a stranger."""
    client, _store, _mid = _client()
    r = client.post(
        f"/meetings/{PLAT}/{NID}/annotate", json={"title": "x"}, headers={"x-user-id": "999"},
    )
    assert r.status_code == 404


# ---- query by metadata: containment, in the store, not on a page ----------------------

def _seed(store, native, metadata):
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=native, status="completed")
    store._meetings[mid]["data"]["metadata"] = metadata
    return mid


def test_list_filters_by_metadata_containment():
    client, store, _mid = _client()
    _seed(store, "deal-one", {"crm_deal": "acme-42", "stage": "discovery"})
    _seed(store, "deal-two", {"crm_deal": "other-1"})
    r = client.get("/meetings", params={"metadata": '{"crm_deal":"acme-42"}'}, headers=H)
    assert r.status_code == 200, r.text
    got = [m["native_meeting_id"] for m in r.json()["meetings"]]
    assert got == ["deal-one"]


def test_metadata_filter_is_containment_not_equality():
    """Extra keys on the meeting must not disqualify it — mirrors JSONB `@>`."""
    client, store, _mid = _client()
    _seed(store, "deal-one", {"crm_deal": "acme-42", "stage": "discovery", "owner": "dmitry"})
    r = client.get("/meetings", params={"metadata": '{"stage":"discovery"}'}, headers=H)
    assert [m["native_meeting_id"] for m in r.json()["meetings"]] == ["deal-one"]


def test_metadata_filter_requires_every_key_to_match():
    client, store, _mid = _client()
    _seed(store, "deal-one", {"crm_deal": "acme-42"})
    r = client.get(
        "/meetings", params={"metadata": '{"crm_deal":"acme-42","stage":"closed"}'}, headers=H,
    )
    assert r.json()["meetings"] == []


def test_malformed_metadata_filter_is_422_not_a_silent_full_list():
    """A filter that silently failed to apply would be a WRONG answer, not a slow one: the caller
    would read an unfiltered list as 'these all match'."""
    client, _store, _mid = _client()
    assert client.get("/meetings", params={"metadata": "not-json"}, headers=H).status_code == 422
    assert client.get("/meetings", params={"metadata": "[1,2]"}, headers=H).status_code == 422
