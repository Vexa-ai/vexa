"""Conformance tests for the sealed ``zaki-read.v1`` runtime route."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource

from meeting_api import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore


TOKEN = "wp-m1-read-token-0123456789abcdef"
USER_ID = 7
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
HEADERS = {
    "x-zaki-read-token": TOKEN,
    "x-zaki-user-id": str(USER_ID),
}


def _contract_validator(shape: str) -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "contracts/zaki-read.v1/zaki-read.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    registry = Registry().with_resource(
        schema["$id"], Resource.from_contents(schema)
    )
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{shape}"},
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _meeting_data() -> dict:
    return {
        "title": "Launch readiness retrospective",
        "attendees": ["Amina", "Jonas", "Maya"],
        "zaki_capture": {
            "state": "authorized",
            "bot_name": "ZAKI Notetaker",
            "tenant_attested": True,
            "tenant_attested_at": "2026-07-16T08:55:00+00:00",
            "tenant_policy_version": "minutes-capture.v1",
        },
        "zaki_read": {"enabled": True},
        "zaki_retention": {
            "state": "open",
            "scope_expiries": {
                "audio": "2026-08-16T10:00:00+00:00",
                "transcript": "2026-10-16T10:00:00+00:00",
                "summary": "2026-10-16T10:00:00+00:00",
            },
            "expired_scopes": [],
        },
        "summary": {
            "text": "The team kept Minutes gated and assigned the privacy follow-up.",
            "updated_at": "2026-07-16T10:06:00+00:00",
        },
    }


def _segments() -> list[dict]:
    return [
        {
            "segment_id": "turn-1",
            "speaker": "Amina",
            "start": 60.0,
            "end": 64.0,
            "absolute_start_time": "2026-07-16T09:01:00+00:00",
            "absolute_end_time": "2026-07-16T09:01:04+00:00",
            "text": "We agreed to keep Minutes gated.",
            "language": "en",
        }
    ]


def _store(*, platform: str = "google_meet") -> InMemoryTranscriptStore:
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=41,
        user_id=USER_ID,
        platform=platform,
        native_meeting_id="private-native-id",
        status="completed",
        start_time="2026-07-16T09:00:00+00:00",
        end_time="2026-07-16T10:00:00+00:00",
        created_at="2026-07-16T08:59:00+00:00",
        updated_at="2026-07-16T10:06:00+00:00",
        data=_meeting_data(),
        segments=_segments(),
    )
    return store


def _client(store: InMemoryTranscriptStore | None = None) -> TestClient:
    return TestClient(
        create_app(
            transcript_store=store or _store(),
            zaki_read_token=TOKEN,
            zaki_read_now=lambda: NOW,
        )
    )


def test_index_maps_real_store_records_to_metadata_only_contract_items():
    response = _client().get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=50",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["truncated"] is False
    assert {item["kind"] for item in body["items"]} == {
        "meeting",
        "transcript",
        "summary",
    }
    assert all("content" not in item for item in body["items"])
    assert "private-native-id" not in response.text


def test_index_orders_by_meeting_occurrence_not_later_reprocessing():
    store = _store()
    store._meetings[41]["start_time"] = "2026-07-16T11:00:00+00:00"
    store._meetings[41]["end_time"] = "2026-07-16T12:00:00+00:00"
    older_data = deepcopy(_meeting_data())
    older_data["title"] = "Older meeting reprocessed later"
    older_data["summary"]["updated_at"] = "2026-07-17T11:30:00+00:00"
    store.seed_meeting(
        meeting_id=42,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="older-private-id",
        status="completed",
        start_time="2026-07-16T12:00:00+02:00",
        end_time="2026-07-16T13:00:00+02:00",
        created_at="2026-07-16T09:59:00+00:00",
        updated_at="2026-07-17T11:30:00+00:00",
        data=older_data,
        segments=_segments(),
    )

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=200",
        headers=HEADERS,
    )

    assert response.status_code == 200
    items = response.json()["items"]
    occurred = [datetime.fromisoformat(item["occurred_at"]) for item in items]
    assert occurred == sorted(occurred, reverse=True)
    assert {item["id"] for item in items[:3]} == {
        "meeting:41",
        "transcript:41",
        "summary:41",
    }


def test_index_uses_owner_metadata_projection_without_transcript_hydration():
    store = _store()
    foreign_data = deepcopy(_meeting_data())
    foreign_data["transcript_viewers"] = [USER_ID]
    store.seed_meeting(
        meeting_id=99,
        user_id=99,
        platform="google_meet",
        native_meeting_id="foreign-private-id",
        status="completed",
        start_time="2026-07-16T10:00:00+00:00",
        end_time="2026-07-16T11:00:00+00:00",
        data=foreign_data,
        segments=_segments(),
    )
    original_list = store.list_meetings
    projection_calls: list[int] = []

    async def owner_metadata_projection(user_id: int):
        projection_calls.append(user_id)
        projected = []
        for meeting in await original_list(user_id):
            if meeting["user_id"] != user_id:
                continue
            data = meeting["data"]
            projected.append({
                **meeting,
                "data": {
                    "title": data["title"],
                    "zaki_capture": data["zaki_capture"],
                    "zaki_read": data["zaki_read"],
                    "zaki_retention": data["zaki_retention"],
                },
                "meeting_available": True,
                "transcript_available": True,
                "summary_available": True,
                "summary_updated_at": data["summary"]["updated_at"],
            })
        return projected

    async def reject_generic_meeting_list(*_args, **_kwargs):
        raise AssertionError("index must use the owner-only metadata projection")

    async def reject_transcript_read(*_args, **_kwargs):
        raise AssertionError("index metadata must not hydrate transcript segments")

    store.list_owned_read_metadata = owner_metadata_projection
    store.list_meetings = reject_generic_meeting_list
    store.get_transcript_by_id = reject_transcript_read

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=200",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert projection_calls == [USER_ID]
    assert {item["id"] for item in response.json()["items"]} == {
        "meeting:41",
        "transcript:41",
        "summary:41",
    }


async def test_inmemory_read_metadata_projection_is_owner_only_and_body_free():
    store = _store()
    store._meetings[41]["data"]["notes"] = "private notes outside the read index"
    store._meetings[41]["data"]["zaki_capture"]["grant_id_sha256"] = "private-grant-hash"
    store._meetings[41]["data"]["zaki_retention"]["internal_policy"] = "private-policy-state"
    foreign_data = deepcopy(_meeting_data())
    foreign_data["transcript_viewers"] = [USER_ID]
    store.seed_meeting(
        meeting_id=99,
        user_id=99,
        platform="google_meet",
        native_meeting_id="foreign-private-id",
        status="completed",
        data=foreign_data,
        segments=_segments(),
    )

    rows = await store.list_owned_read_metadata(USER_ID)

    assert [row["id"] for row in rows] == [41]
    assert rows[0]["transcript_available"] is True
    assert "segments" not in rows[0]
    assert "native_meeting_id" not in rows[0]
    serialized = json.dumps(rows)
    assert "private-native-id" not in serialized
    assert "team kept Minutes" not in serialized
    assert "private notes outside the read index" not in serialized
    assert "private-grant-hash" not in serialized
    assert "private-policy-state" not in serialized


def test_read_plane_is_default_off_token_authenticated_user_scoped_and_read_only():
    path = f"/api/zaki/read/v1/{USER_ID}/index"

    disabled = TestClient(create_app(transcript_store=_store())).get(path, headers=HEADERS)
    assert disabled.status_code == 401
    assert disabled.json()["error"]["code"] == "bad_token"

    bad_token = _client().get(path, headers={**HEADERS, "x-zaki-read-token": "wrong"})
    assert bad_token.status_code == 401
    assert bad_token.json()["error"]["code"] == "bad_token"

    mismatch = _client().get(path, headers={**HEADERS, "x-zaki-user-id": "8"})
    assert mismatch.status_code == 404
    assert mismatch.json()["error"]["code"] == "unknown_user"

    mutation = _client().post(path, headers=HEADERS)
    assert mutation.status_code == 405
    assert mutation.json()["error"]["code"] == "read_only"


@pytest.mark.parametrize("unsafe_token", ["too-short", " x" * 16, "x\t" * 16])
def test_unsafe_configured_token_fails_closed(unsafe_token: str):
    response = TestClient(
        create_app(transcript_store=_store(), zaki_read_token=unsafe_token)
    ).get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers={
            "x-zaki-read-token": unsafe_token,
            "x-zaki-user-id": str(USER_ID),
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bad_token"


def test_index_cursor_continues_with_a_non_overlapping_bounded_page():
    store = _store()
    store.seed_meeting(
        meeting_id=42,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="another-private-id",
        status="completed",
        start_time="2026-07-15T09:00:00+00:00",
        end_time="2026-07-15T10:00:00+00:00",
        created_at="2026-07-15T08:59:00+00:00",
        updated_at="2026-07-15T10:06:00+00:00",
        data=_meeting_data(),
        segments=_segments(),
    )
    client = _client(store)

    first = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=2",
        headers=HEADERS,
    ).json()
    assert first["truncated"] is True
    assert first["next_cursor"]

    second_response = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=2&cursor={first['next_cursor']}",
        headers=HEADERS,
    )
    assert second_response.status_code == 200
    second = second_response.json()
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )


def test_index_since_filters_update_time_and_is_bound_into_the_cursor():
    client = _client()

    invalid = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index?since=not-a-time",
        headers=HEADERS,
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "bad_since"

    filtered = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index?since=2026-07-16T10:06:00Z&limit=2",
        headers=HEADERS,
    ).json()
    assert filtered["truncated"] is True
    assert all(item["updated_at"] >= "2026-07-16T10:06:00" for item in filtered["items"])

    widened = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=2&cursor={filtered['next_cursor']}",
        headers=HEADERS,
    )
    assert widened.status_code == 400
    assert widened.json()["error"]["code"] == "bad_cursor"


def test_structurally_invalid_signed_cursor_is_rejected():
    payload = json.dumps([]).encode()
    signature = hmac.new(TOKEN.encode(), payload, hashlib.sha256).digest()
    cursor = base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode()

    response = _client().get(
        f"/api/zaki/read/v1/{USER_ID}/index?cursor={cursor}",
        headers=HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_cursor"


def test_item_projects_meeting_transcript_and_summary_contract_variants():
    client = _client()

    meeting = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/meeting:41",
        headers=HEADERS,
    )
    assert meeting.status_code == 200
    assert meeting.json()["item"]["content"] == {
        "platform": "google_meet",
        "started_at": "2026-07-16T09:00:00+00:00",
        "ended_at": "2026-07-16T10:00:00+00:00",
        "attendees": ["Amina", "Jonas", "Maya"],
    }

    transcript = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )
    assert transcript.status_code == 200
    transcript_item = transcript.json()["item"]
    assert transcript_item["content"]["format"] == "speaker_turns"
    assert transcript_item["content"]["turns"] == [
        {
            "speaker": "Amina",
            "started_at": "2026-07-16T09:01:00+00:00",
            "ended_at": "2026-07-16T09:01:04+00:00",
            "text": "We agreed to keep Minutes gated.",
        }
    ]
    assert transcript_item["capture_notice"]["bot_visible"] is True

    summary = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/summary:41",
        headers=HEADERS,
    )
    assert summary.status_code == 200
    summary_item = summary.json()["item"]
    assert summary_item["content"]["format"] == "summary"
    assert "capture_notice" not in summary_item


def test_meeting_item_hides_platforms_outside_the_sealed_contract():
    client = _client(_store(platform="browser_session"))

    unsupported = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/meeting:41",
        headers=HEADERS,
    )
    unknown = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/meeting:999",
        headers=HEADERS,
    )

    assert unsupported.status_code == unknown.status_code == 404
    assert unsupported.json() == unknown.json()
    index = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers=HEADERS,
    )
    assert all(item["id"] != "meeting:41" for item in index.json()["items"])


def test_meeting_index_hides_metadata_whose_detail_shape_is_invalid():
    store = _store()
    store._meetings[41]["data"]["attendees"] = ["Amina", 42]
    client = _client(store)

    item = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/meeting:41",
        headers=HEADERS,
    )
    index = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers=HEADERS,
    )

    assert item.status_code == 404
    assert all(entry["id"] != "meeting:41" for entry in index.json()["items"])


def test_runtime_responses_validate_against_the_sealed_contract_schema():
    client = _client()
    index = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers=HEADERS,
    ).json()
    _contract_validator("IndexResponse").validate(index)

    item_validator = _contract_validator("ItemResponse")
    for item_id in ("meeting:41", "transcript:41", "summary:41"):
        response = client.get(
            f"/api/zaki/read/v1/{USER_ID}/item/{item_id}",
            headers=HEADERS,
        )
        assert response.status_code == 200
        item_validator.validate(response.json())


def test_malformed_summary_update_time_fails_closed():
    data = _meeting_data()
    data["summary"]["updated_at"] = "not-a-time"
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=41,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="private-native-id",
        status="completed",
        start_time="2026-07-16T09:00:00+00:00",
        end_time="2026-07-16T10:00:00+00:00",
        data=data,
        segments=_segments(),
    )
    client = _client(store)

    index = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers=HEADERS,
    )
    assert index.status_code == 200
    assert all(item["id"] != "summary:41" for item in index.json()["items"])

    item = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/summary:41",
        headers=HEADERS,
    )
    assert item.status_code == 404
    assert item.json()["error"]["code"] == "unknown_item"


@pytest.mark.parametrize(
    "case",
    [
        "missing_agent_read_opt_in",
        "invisible_capture",
        "expired_transcript",
        "invalid_attestation_time",
        "future_meeting",
    ],
)
def test_unreadable_transcripts_are_hidden_from_index_and_item(case: str):
    data = deepcopy(_meeting_data())
    start_time = "2026-07-16T09:00:00+00:00"
    if case == "missing_agent_read_opt_in":
        del data["zaki_read"]
    elif case == "invisible_capture":
        data["zaki_capture"]["bot_name"] = "Hidden bot"
    elif case == "expired_transcript":
        data["zaki_retention"]["scope_expiries"]["transcript"] = "2026-07-17T11:59:59+00:00"
    elif case == "invalid_attestation_time":
        data["zaki_capture"]["tenant_attested_at"] = "not-a-time"
    elif case == "future_meeting":
        start_time = "2026-07-18T09:00:00+00:00"

    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=41,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="private-native-id",
        status="completed",
        start_time=start_time,
        end_time="2026-07-18T10:00:00+00:00" if case == "future_meeting" else "2026-07-16T10:00:00+00:00",
        created_at=start_time,
        updated_at=start_time,
        data=data,
        segments=_segments(),
    )
    client = _client(store)

    index = client.get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers=HEADERS,
    )
    assert all(item["id"] != "transcript:41" for item in index.json()["items"])

    item = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )
    assert item.status_code == 404
    assert item.json()["error"]["code"] == "unknown_item"


def test_search_filters_before_pagination_and_returns_metadata_only():
    store = _store()
    expired = deepcopy(_meeting_data())
    expired["summary"]["text"] = _meeting_data()["summary"]["text"]
    expired["zaki_retention"]["scope_expiries"]["summary"] = "2026-07-17T11:59:59+00:00"
    store.seed_meeting(
        meeting_id=42,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="expired-private-id",
        status="completed",
        start_time="2026-07-15T09:00:00+00:00",
        end_time="2026-07-15T10:00:00+00:00",
        data=expired,
        segments=_segments(),
    )

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/search?q=privacy%20follow-up&limit=20",
        headers=HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    _contract_validator("IndexResponse").validate(body)
    assert [item["id"] for item in body["items"]] == ["summary:41"]
    assert body["truncated"] is False
    assert all("content" not in item for item in body["items"])
    assert "team kept Minutes" not in response.text


@pytest.mark.parametrize("case", ["out_of_order", "end_before_start"])
def test_transcript_turn_order_and_ranges_fail_closed(case: str):
    segments = [
        {
            **_segments()[0],
            "segment_id": "turn-later",
            "absolute_start_time": "2026-07-16T09:05:00+00:00",
            "absolute_end_time": "2026-07-16T09:06:00+00:00",
        },
        {
            **_segments()[0],
            "segment_id": "turn-earlier",
            "absolute_start_time": "2026-07-16T09:04:00+00:00",
            "absolute_end_time": "2026-07-16T09:04:30+00:00",
        },
    ]
    if case == "end_before_start":
        segments = [{
            **_segments()[0],
            "absolute_start_time": "2026-07-16T09:05:00+00:00",
            "absolute_end_time": "2026-07-16T09:04:59+00:00",
        }]
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=41,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="private-native-id",
        status="completed",
        start_time="2026-07-16T09:00:00+00:00",
        end_time="2026-07-16T10:00:00+00:00",
        data=_meeting_data(),
        segments=segments,
    )

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_item"
    index = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/index",
        headers=HEADERS,
    )
    assert all(item["id"] != "transcript:41" for item in index.json()["items"])


@pytest.mark.parametrize("limit", ["not-a-number", "0", "999999"])
def test_index_limit_is_bounded_without_leaking_framework_validation(limit: str):
    response = _client().get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit={limit}",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) <= 200


def test_summary_variant_does_not_materialize_oversized_transcript():
    store = _store()
    store._meetings[41]["segments"]["turn-1"]["text"] = "x" * 65_537

    async def reject_transcript_read(*_args, **_kwargs):
        raise AssertionError("summary fallback must not load transcript segments")

    store.get_transcript_by_id = reject_transcript_read

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41?variant=summary",
        headers=HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    _contract_validator("ItemResponse").validate(body)
    assert body["item"]["content"] == {
        "format": "summary",
        "text": "The team kept Minutes gated and assigned the privacy follow-up.",
    }


def test_summary_variant_fails_closed_when_no_stored_summary_exists():
    store = _store()
    del store._meetings[41]["data"]["summary"]

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41?variant=summary",
        headers=HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_item"


def test_summary_variant_respects_the_stored_summary_retention_scope():
    store = _store()
    store._meetings[41]["data"]["zaki_retention"]["scope_expiries"]["summary"] = (
        "2026-07-17T11:59:59+00:00"
    )

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41?variant=summary",
        headers=HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_item"


def test_denied_full_transcript_does_not_materialize_sensitive_segments():
    store = _store()
    store._meetings[41]["data"]["zaki_retention"]["scope_expiries"]["transcript"] = (
        "2026-07-17T11:59:59+00:00"
    )

    async def reject_transcript_read(*_args, **_kwargs):
        raise AssertionError("retention must be authorized before transcript segments are loaded")

    store.get_transcript_by_id = reject_transcript_read

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_item"


@pytest.mark.parametrize("item_id", ["meeting:41", "summary:41"])
def test_metadata_backed_items_do_not_materialize_transcript_segments(item_id: str):
    store = _store()

    async def reject_transcript_read(*_args, **_kwargs):
        raise AssertionError("metadata-backed items must not load transcript segments")

    store.get_transcript_by_id = reject_transcript_read

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/{item_id}",
        headers=HEADERS,
    )

    assert response.status_code == 200
    _contract_validator("ItemResponse").validate(response.json())


@pytest.mark.parametrize("language", ["", "x", "x" * 36])
def test_transcript_omits_language_outside_the_sealed_bounds(language: str):
    store = _store()
    store._meetings[41]["segments"]["turn-1"]["language"] = language

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    _contract_validator("ItemResponse").validate(body)
    assert "language" not in body["item"]["content"]


def test_turn_schema_bound_is_distinct_from_aggregate_content_cap():
    invalid_store = _store()
    invalid_store._meetings[41]["segments"]["turn-1"]["text"] = "x" * 65_537
    invalid = _client(invalid_store).get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )
    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "unknown_item"

    large_store = InMemoryTranscriptStore()
    large_store.seed_meeting(
        meeting_id=41,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="private-native-id",
        status="completed",
        start_time="2026-07-16T09:00:00+00:00",
        end_time="2026-07-16T10:00:00+00:00",
        data=_meeting_data(),
        segments=[
            {
                **_segments()[0],
                "segment_id": f"turn-{index}",
                "start": float(index),
                "end": float(index) + 0.5,
                "absolute_start_time": f"2026-07-16T09:0{index}:00+00:00",
                "absolute_end_time": f"2026-07-16T09:0{index}:30+00:00",
                "text": "x" * 60_000,
            }
            for index in range(1, 6)
        ],
    )
    client = _client(large_store)
    full = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41",
        headers=HEADERS,
    )
    assert full.status_code == 413
    assert full.json()["error"]["code"] == "item_too_large"
    assert "x" * 100 not in full.text

    summary = client.get(
        f"/api/zaki/read/v1/{USER_ID}/item/transcript:41?variant=summary",
        headers=HEADERS,
    )
    assert summary.status_code == 200
    assert summary.json()["item"]["content"]["format"] == "summary"


@pytest.mark.parametrize("route", ["index", "search?q=Minutes", "item/transcript:41"])
def test_explicitly_disabled_read_scope_returns_scope_disabled(route: str):
    data = _meeting_data()
    data["zaki_read"]["enabled"] = False
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=41,
        user_id=USER_ID,
        platform="google_meet",
        native_meeting_id="private-native-id",
        status="completed",
        start_time="2026-07-16T09:00:00+00:00",
        end_time="2026-07-16T10:00:00+00:00",
        data=data,
        segments=_segments(),
    )

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/{route}",
        headers=HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "scope_disabled"


def test_index_lists_ordinarily_stopped_meetings_but_hides_privacy_withdrawals():
    """The archive must OUTLIVE the capture (owner round-5): stopping tombstones the
    authority (state=withdrawn, reason=capture_stopped) — requiring state=='authorized'
    made every successfully-captured meeting VANISH from the index at stop, while failed
    never-stopped meetings kept showing. A privacy tombstone (consent_withdrawn, or any
    unknown reason: fail closed) still hides the meeting."""
    stopped = _meeting_data()
    stopped["zaki_capture"] = {
        **stopped["zaki_capture"],
        "state": "withdrawn",
        "withdrawal_reason": "capture_stopped",
        "withdrawn_at": "2026-07-16T10:10:00+00:00",
    }
    withdrawn = _meeting_data()
    withdrawn["zaki_capture"] = {
        **withdrawn["zaki_capture"],
        "state": "withdrawn",
        "withdrawal_reason": "consent_withdrawn",
        "withdrawn_at": "2026-07-16T10:10:00+00:00",
    }
    store = InMemoryTranscriptStore()
    for mid, native, data in ((41, "stopped-native", stopped), (42, "withdrawn-native", withdrawn)):
        store.seed_meeting(
            meeting_id=mid, user_id=USER_ID, platform="google_meet",
            native_meeting_id=native, status="completed",
            start_time="2026-07-16T09:00:00+00:00", end_time="2026-07-16T10:00:00+00:00",
            created_at="2026-07-16T08:59:00+00:00", updated_at="2026-07-16T10:10:00+00:00",
            data=data, segments=_segments(),
        )

    response = _client(store).get(
        f"/api/zaki/read/v1/{USER_ID}/index?limit=50", headers=HEADERS,
    )

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert any(i.startswith("meeting:41") or i == "meeting:41" for i in ids), ids
    assert not any("42" in i for i in ids), ids
