"""GET /transcripts/search — full-text search over the caller's OWN transcript segments.

Answers what metadata cannot: not "meetings I tagged X" but "meetings where someone SAID X".

SCOPE OF THIS FILE: the route's CONTRACT — owner scoping, filters, paging, hit shape, refusals.
NOT the search semantics. The in-memory fake is a crude stand-in (whole-word AND, quoted phrases,
`-negation`); it does not stem, does not rank by cover density, and does not implement `or`.
Real tsquery behaviour is a Postgres property and meeting-api has no `requires_docker` lane the
way admin-api does — asserting it here would produce green tests over wrong production code.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER, OTHER = 7, 99
H = {"x-user-id": str(USER)}
PLAT = "google_meet"


class _NullRedis:
    async def publish(self, channel, data):
        return None


def _seg(i, text, speaker="Dmitriy", start=0.0):
    return {"segment_id": f"s{i}", "start": start, "end": start + 2, "text": text,
            "speaker": speaker, "language": "en"}


def _client():
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=USER, platform=PLAT, native_meeting_id="acme-call", meeting_id=1,
        segments=[
            _seg(1, "we should revisit the pricing before the renewal", start=0),
            _seg(2, "the latency numbers look fine", speaker="Ana", start=10),
            _seg(3, "lets align on scope before the demo", start=20),
        ],
    )
    store.seed_meeting(
        user_id=USER, platform="teams", native_meeting_id="globex-call", meeting_id=2,
        segments=[_seg(4, "pricing came up again on the globex call", start=0)],
    )
    # Another tenant's meeting, same words — must never surface.
    store.seed_meeting(
        user_id=OTHER, platform=PLAT, native_meeting_id="secret-call", meeting_id=3,
        segments=[_seg(5, "our pricing is confidential", speaker="Someone Else", start=0)],
    )
    return TestClient(create_app(store, redis=_NullRedis())), store


def _search(client, q, **params):
    return client.get("/transcripts/search", params={"q": q, **params}, headers=H)


# ---- the core question -------------------------------------------------------------

def test_finds_what_was_said_across_meetings():
    client, _ = _client()
    r = _search(client, "pricing")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert {h["native_meeting_id"] for h in body["hits"]} == {"acme-call", "globex-call"}


def test_a_hit_carries_the_identity_needed_to_fetch_the_transcript():
    """The whole point of a hit: an agent must be able to feed it straight into
    get_meeting_transcript without inventing anything."""
    client, _ = _client()
    hit = _search(client, "latency").json()["hits"][0]
    for field in ("platform", "native_meeting_id", "start", "end", "speaker", "snippet", "rank"):
        assert field in hit, f"hit is missing {field}"
    assert hit["platform"] == PLAT and hit["native_meeting_id"] == "acme-call"
    assert hit["speaker"] == "Ana"


def test_snippet_not_the_whole_corpus():
    client, _ = _client()
    hit = _search(client, "latency").json()["hits"][0]
    assert "latency" in hit["snippet"].lower()


# ---- isolation: the property that must never regress --------------------------------

def test_another_tenants_transcript_never_surfaces():
    """Owner-scoped, fail-closed. A search that over-returns is a disclosure, not a bug."""
    client, _ = _client()
    hits = _search(client, "pricing").json()["hits"]
    assert all(h["native_meeting_id"] != "secret-call" for h in hits)
    assert all(h["speaker"] != "Someone Else" for h in hits)


def test_the_other_tenant_can_see_their_own():
    client, _ = _client()
    r = client.get("/transcripts/search", params={"q": "pricing"}, headers={"x-user-id": str(OTHER)})
    assert [h["native_meeting_id"] for h in r.json()["hits"]] == ["secret-call"]


# ---- filters + paging ---------------------------------------------------------------

def test_platform_filter():
    client, _ = _client()
    hits = _search(client, "pricing", platform="teams").json()["hits"]
    assert [h["native_meeting_id"] for h in hits] == ["globex-call"]


def test_single_meeting_filter():
    client, _ = _client()
    hits = _search(client, "pricing", native_meeting_id="acme-call").json()["hits"]
    assert [h["native_meeting_id"] for h in hits] == ["acme-call"]


def test_limit_and_offset():
    client, _ = _client()
    assert len(_search(client, "pricing", limit=1).json()["hits"]) == 1
    first = _search(client, "pricing", limit=1).json()["hits"][0]
    second = _search(client, "pricing", limit=1, offset=1).json()["hits"][0]
    assert first != second


# ---- refusals ------------------------------------------------------------------------

def test_blank_query_is_refused():
    client, _ = _client()
    assert client.get("/transcripts/search", params={"q": "   "}, headers=H).status_code == 422


def test_missing_query_is_refused():
    client, _ = _client()
    assert client.get("/transcripts/search", headers=H).status_code == 422


def test_no_identity_is_401():
    client, _ = _client()
    assert client.get("/transcripts/search", params={"q": "pricing"}).status_code == 401


def test_no_match_is_an_empty_result_not_an_error():
    client, _ = _client()
    body = _search(client, "zzzznotpresent").json()
    assert body["count"] == 0 and body["hits"] == []


def test_search_is_not_swallowed_as_a_platform_name():
    """`/transcripts/search` must route to search, not to
    `/transcripts/{platform}/{native}` with platform='search'."""
    client, _ = _client()
    assert _search(client, "pricing").status_code == 200
