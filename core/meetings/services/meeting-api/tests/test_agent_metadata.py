"""#1064 — agent-owned meeting metadata: attach in-call (POST /bots), amend (PATCH …/metadata),
filter (GET /meetings?custom.*), and surface it in responses.

Flywheel iteration 2 (stacks on #1062, which lets a bot-scoped key READ its own meetings): an agent
attaches its own classification to a meeting under the RESERVED ``data['custom']`` namespace, then
filters/retrieves its corpus by it. These tests drive the SHIPPED meeting-api handlers over the
in-memory fakes (TestClient, offline) — the same fakes that mirror the SqlAlchemy store — so the
behaviour proven here is the shipped behaviour.

Load-bearing invariants pinned:
  * POST /bots persists ``data.custom.*`` alongside the spawn keys WITHOUT clobbering them.
  * PATCH …/metadata MERGES into ``data.custom`` (never replaces) and never touches sibling ``data``
    keys (``recording`` / ``config`` — the recordings/lifecycle writers' territory).
  * GET /meetings?custom.<k>=<v> returns only meetings whose ``data.custom`` contains the pair.
  * Cross-user isolation: user A's filter never returns user B's meetings.
  * The metadata surfaces in the /meetings + /transcripts responses (recording/config stay cleaned).
  * Abuse (nested blob / oversize / non-object) is a typed 422 at the boundary, never a 500.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher
from meeting_api.metadata import MAX_METADATA_BYTES, MetadataError, validate_custom_metadata

USER = 7
OTHER = 99
HEADERS = {"x-user-id": str(USER)}
OTHER_HEADERS = {"x-user-id": str(OTHER)}
SECRET = "test-admin-token"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    # POST /bots mints a MeetingToken signed with ADMIN_TOKEN; the autouse conftest fixture already
    # sets the STT creds so a default transcribe_enabled spawn is served (not 503).
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)


def _client(store=None, repo=None):
    return TestClient(create_app(
        transcript_store=store or InMemoryTranscriptStore(),
        meeting_repo=repo or InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(),
        command_publisher=InMemoryCommandPublisher(),
    ))


# ── unit: the boundary validator (pure) ─────────────────────────────────────────────────────────

def test_validate_accepts_flat_object_with_scalar_and_list_values():
    out = validate_custom_metadata({"project": "apollo", "priority": 3, "tags": ["sales", "q3"],
                                    "billable": True, "note": None})
    assert out == {"project": "apollo", "priority": 3, "tags": ["sales", "q3"],
                   "billable": True, "note": None}


def test_validate_rejects_non_object():
    with pytest.raises(MetadataError):
        validate_custom_metadata("just-a-string")
    with pytest.raises(MetadataError):
        validate_custom_metadata(["a", "b"])


def test_validate_rejects_nested_object():
    with pytest.raises(MetadataError):
        validate_custom_metadata({"deep": {"nope": 1}})


def test_validate_rejects_list_of_objects():
    with pytest.raises(MetadataError):
        validate_custom_metadata({"tags": [{"nested": 1}]})


def test_validate_rejects_oversize():
    with pytest.raises(MetadataError):
        validate_custom_metadata({"blob": "x" * (MAX_METADATA_BYTES + 1)})


# ── POST /bots — attach in-call, under data.custom, no clobber of the spawn keys ─────────────────

def test_post_bots_attaches_custom_metadata():
    repo = InMemoryMeetingRepo()
    client = _client(repo=repo)
    r = client.post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "metadata": {"project": "apollo", "tags": ["sales"]},
    })
    assert r.status_code == 201, r.text
    row = next(iter(repo._meetings.values()))
    assert row["data"]["custom"] == {"project": "apollo", "tags": ["sales"]}
    # No-clobber WITHIN the create: the spawn keys written in the same row survive alongside custom.
    assert row["data"]["recording_enabled"] is True or row["data"]["recording_enabled"] is False
    assert "service_authority" in row["data"]
    assert row["data"]["constructed_meeting_url"] == "https://meet.google.com/abc-defg-hij"


def test_post_bots_without_metadata_has_no_custom_namespace():
    repo = InMemoryMeetingRepo()
    r = _client(repo=repo).post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "no-meta",
    })
    assert r.status_code == 201, r.text
    assert "custom" not in next(iter(repo._meetings.values()))["data"]


@pytest.mark.parametrize("bad", [
    {"deep": {"nope": 1}},          # nested object
    "not-an-object",                # non-object
    {"blob": "x" * (MAX_METADATA_BYTES + 1)},  # oversize
])
def test_post_bots_rejects_bad_metadata_422(bad):
    r = _client().post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "reject", "metadata": bad,
    })
    assert r.status_code == 422, r.text


# ── PATCH /meetings/{platform}/{native}/metadata — MERGE, no clobber of recording/config ─────────

def _seed(store, *, user_id=USER, native="abc-defg-hij", data=None):
    return store.seed_meeting(
        user_id=user_id, platform="google_meet", native_meeting_id=native,
        status="active", data=data or {},
    )


def test_patch_metadata_merges_not_replaces():
    store = InMemoryTranscriptStore()
    _seed(store, data={"custom": {"project": "apollo"}})
    r = _client(store=store).patch(
        "/meetings/google_meet/abc-defg-hij/metadata", headers=HEADERS,
        json={"tags": ["sales"]},
    )
    assert r.status_code == 200, r.text
    # project (attached earlier) survives; tags (this amend) is added — a MERGE, not a replace.
    assert r.json()["custom"] == {"project": "apollo", "tags": ["sales"]}


def test_patch_metadata_no_clobber_of_recording_and_config():
    store = InMemoryTranscriptStore()
    # Seed the sibling data keys FIRST (the recordings/lifecycle writers' territory), then amend metadata.
    recording = {"recordings": [{"id": "r1", "storage_path": "s3://rec/1"}]}
    seeded = {
        "recording": {"master": "s3://rec/master.wav", "chunk_count": 3},
        "config": {"transcribe_enabled": True, "language": "en"},
        "custom": {"project": "apollo"},
        **recording,
    }
    _seed(store, data=dict(seeded))
    r = _client(store=store).patch(
        "/meetings/google_meet/abc-defg-hij/metadata", headers=HEADERS,
        json={"tags": ["sales"], "owner": "agent-42"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["custom"] == {"project": "apollo", "tags": ["sales"], "owner": "agent-42"}
    # The sibling keys are byte-for-byte intact — the merge touched ONLY data['custom'].
    stored = store._meetings[1]["data"]
    assert stored["recording"] == seeded["recording"]
    assert stored["config"] == seeded["config"]
    assert stored["recordings"] == recording["recordings"]


def test_patch_metadata_404_when_not_owned():
    store = InMemoryTranscriptStore()
    _seed(store, user_id=OTHER)  # owned by someone else
    r = _client(store=store).patch(
        "/meetings/google_meet/abc-defg-hij/metadata", headers=HEADERS, json={"tags": ["x"]},
    )
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("bad", [{}, {"deep": {"n": 1}}, {"tags": [{"n": 1}]}])
def test_patch_metadata_422_on_bad_body(bad):
    store = InMemoryTranscriptStore()
    _seed(store)
    r = _client(store=store).patch(
        "/meetings/google_meet/abc-defg-hij/metadata", headers=HEADERS, json=bad,
    )
    assert r.status_code == 422, r.text


# ── GET /meetings?custom.<k>=<v> — filter by attached metadata ───────────────────────────────────

def _seed_two_projects(store):
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="apollo-mtg",
                       status="active", created_at="2026-06-20T09:00:00Z",
                       data={"custom": {"project": "apollo"}})
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="zephyr-mtg",
                       status="active", created_at="2026-06-20T09:05:00Z",
                       data={"custom": {"project": "zephyr"}})


def test_filter_returns_only_matching():
    store = InMemoryTranscriptStore()
    _seed_two_projects(store)
    r = _client(store=store).get("/meetings?custom.project=apollo", headers=HEADERS)
    assert r.status_code == 200, r.text
    natives = [m["native_meeting_id"] for m in r.json()["meetings"]]
    assert natives == ["apollo-mtg"]


def test_filter_non_matching_returns_none():
    store = InMemoryTranscriptStore()
    _seed_two_projects(store)
    r = _client(store=store).get("/meetings?custom.project=does-not-exist", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["meetings"] == []


def test_filter_underscore_spelling_also_works():
    store = InMemoryTranscriptStore()
    _seed_two_projects(store)
    r = _client(store=store).get("/meetings?custom_project=zephyr", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert [m["native_meeting_id"] for m in r.json()["meetings"]] == ["zephyr-mtg"]


def test_filter_requires_all_pairs():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="m1",
                       status="active", data={"custom": {"project": "apollo", "tier": "gold"}})
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="m2",
                       status="active", data={"custom": {"project": "apollo", "tier": "silver"}})
    r = _client(store=store).get("/meetings?custom.project=apollo&custom.tier=gold", headers=HEADERS)
    assert [m["native_meeting_id"] for m in r.json()["meetings"]] == ["m1"]


def test_unfiltered_list_returns_all():
    store = InMemoryTranscriptStore()
    _seed_two_projects(store)
    r = _client(store=store).get("/meetings", headers=HEADERS)
    assert len(r.json()["meetings"]) == 2


# ── cross-user isolation: A's filter never returns B's meetings ──────────────────────────────────

def test_filter_cross_user_isolation():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="a-mtg",
                       status="active", data={"custom": {"project": "apollo"}})
    store.seed_meeting(user_id=OTHER, platform="google_meet", native_meeting_id="b-mtg",
                       status="active", data={"custom": {"project": "apollo"}})
    # Both classified project=apollo, but each user only sees their own.
    r_a = _client(store=store).get("/meetings?custom.project=apollo", headers=HEADERS)
    assert [m["native_meeting_id"] for m in r_a.json()["meetings"]] == ["a-mtg"]
    r_b = _client(store=store).get("/meetings?custom.project=apollo", headers=OTHER_HEADERS)
    assert [m["native_meeting_id"] for m in r_b.json()["meetings"]] == ["b-mtg"]


# ── exposure: custom surfaces in the list + transcript responses ─────────────────────────────────

def test_custom_surfaces_in_meetings_list_row():
    store = InMemoryTranscriptStore()
    _seed(store, data={"custom": {"project": "apollo"}, "recordings": [{"id": "r1"}]})
    row = _client(store=store).get("/meetings", headers=HEADERS).json()["meetings"][0]
    # custom is a LIGHT key → surfaces in the list; recordings is a HEAVY key → dropped from the list.
    assert row["data"]["custom"] == {"project": "apollo"}
    assert "recordings" not in row["data"]


def test_custom_surfaces_in_transcript_response():
    store = InMemoryTranscriptStore()
    _seed(store, data={"custom": {"project": "apollo"}})
    doc = _client(store=store).get("/transcripts/google_meet/abc-defg-hij", headers=HEADERS).json()
    assert doc["data"]["custom"] == {"project": "apollo"}


# ── store-level: the merge touches ONLY data['custom'] (the no-clobber mutator property) ─────────

async def test_store_merge_preserves_sibling_keys():
    store = InMemoryTranscriptStore()
    _seed(store, data={"recording": {"master": "x"}, "config": {"lang": "en"},
                       "custom": {"a": 1}})
    custom = await store.merge_custom_metadata(USER, "google_meet", "abc-defg-hij", {"b": 2})
    assert custom == {"a": 1, "b": 2}
    data = store._meetings[1]["data"]
    assert data["recording"] == {"master": "x"} and data["config"] == {"lang": "en"}
