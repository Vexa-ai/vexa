"""The archive-connector read surface: record-keyed transcripts, explicit empty state, honest filters.

Every fixture below is a SHAPE MEASURED in a production account (read-only sweep, 2026-08-16, 747
meetings / 284 transcript fetches) — not an invented edge case. The sweep found three defects, and
this module is the offline proof of each fix, driving SHIPPED code over the in-memory fakes:

  1. **Record-keyed access.** 61.7% of the archive carried NO ``native_meeting_id``, and the only
     transcript route was native-keyed, so those records were unreachable by any route. Two more
     stored a native id that is a full URL (``https://teams.live.com/meet/9366473044740?p=…``) —
     interpolated into ``/transcripts/{platform}/{native}`` its slashes and query string reshape the
     path, so it 404s permanently. And 46 links carried more than one record (largest: 8 runs behind
     one recurring link) with only the newest reachable. Net retrievable: 166 of 747 = 22.2%.
     ``GET /meetings/{meeting_id}/transcript`` addresses all three; before it, that path answered
     **405** — ``/meetings/{a}/{b}`` matched the native-keyed PATCH/DELETE pair, which registers no GET.
  2. **Explicit empty state.** 60 addressable meetings answered ``200`` with ``segments: []``. A
     consumer that trusts the status code builds an archive of empty rows.
  3. **Honest filters.** ``GET /meetings?since=…`` / ``?updated_after=…`` answered ``200`` + the FULL
     list — an incremental consumer reads that as "nothing changed", which is the worst failure shape
     because it is indistinguishable from success.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

from collector_contracts import assert_api_conforms

USER = 7
OTHER = 999
GATEWAY_HEADERS = {"x-user-id": str(USER)}

# The URL-shaped native id the sweep found on meetings 2396 + 2408 (both permanently 404 on the
# native path). Kept verbatim — the point is that it contains `/`, `?` and `=`.
URL_NATIVE_ID = "https://teams.live.com/meet/9366473044740?p=AbCdEf123456"

# The largest collapse set the sweep found: eight google_meet runs on ONE recurring link, of which
# only the newest (15496) is reachable natively. Ids + timestamps are the harvested ones.
COLLAPSE_NATIVE_ID = "mue-bydo-aaf"
COLLAPSE_SET = [
    (7754, "2026-02-23T10:03:25Z"),
    (8483, "2026-03-09T10:01:01Z"),
    (9822, "2026-04-06T09:04:03Z"),
    (10417, "2026-04-20T08:58:21Z"),
    (12026, "2026-05-08T16:39:02Z"),
    (12028, "2026-05-08T16:48:01Z"),
    (12613, "2026-05-18T08:59:25Z"),
    (15496, "2026-06-15T09:03:14Z"),
]
COLLAPSE_WINNER = 15496


def _segment(text: str, *, start: float = 1.0) -> dict:
    return {
        "segment_id": f"ch-0:1:{start}", "start": start, "end": start + 1.5, "text": text,
        "language": "en", "speaker": "spk-Anna", "completed": True,
    }


def _archive_store() -> tuple[InMemoryTranscriptStore, dict]:
    """The four harvested shapes in one account: a collapse set, a URL-shaped native id, a record
    with NO native id, and a completed meeting whose transcript is empty."""
    store = InMemoryTranscriptStore()
    ids: dict = {}

    for mid, created in COLLAPSE_SET:
        store.seed_meeting(
            user_id=USER, meeting_id=mid, platform="google_meet",
            native_meeting_id=COLLAPSE_NATIVE_ID, status="completed",
            created_at=created, updated_at=created,
            segments=[_segment(f"run {mid}")],
        )
    ids["collapse"] = [mid for mid, _ in COLLAPSE_SET]

    ids["url_native"] = store.seed_meeting(
        user_id=USER, meeting_id=2396, platform="teams", native_meeting_id=URL_NATIVE_ID,
        status="completed", created_at="2026-01-14T11:00:00Z", updated_at="2026-01-14T12:00:00Z",
        segments=[_segment("teams live link run")],
    )
    ids["null_native"] = store.seed_meeting(
        user_id=USER, meeting_id=4100, platform="google_meet", native_meeting_id=None,
        status="completed", created_at="2026-02-01T11:00:00Z", updated_at="2026-02-01T12:00:00Z",
        segments=[_segment("no native id on this record")],
    )
    ids["empty"] = store.seed_meeting(
        user_id=USER, meeting_id=12038, platform="zoom", native_meeting_id="99887766",
        status="completed", created_at="2026-05-08T17:59:01Z", updated_at="2026-05-08T18:30:00Z",
        segments=[],
    )
    return store, ids


def _client(store: InMemoryTranscriptStore) -> TestClient:
    return TestClient(create_app(store, redis=None))


# ── 1. record-keyed transcript access ─────────────────────────────────────────────────────────────

def test_record_keyed_transcript_conforms_and_is_no_longer_405():
    store, ids = _archive_store()
    r = _client(store).get(f"/meetings/{COLLAPSE_WINNER}/transcript", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert_api_conforms("TranscriptionResponse", body)
    assert body["id"] == COLLAPSE_WINNER


def test_record_with_null_native_id_is_retrievable_by_record_key():
    """61.7% of the harvested archive. The native path cannot express it at all."""
    store, ids = _archive_store()
    r = _client(store).get(f"/meetings/{ids['null_native']}/transcript", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["native_meeting_id"] is None
    assert [s["text"] for s in body["segments"]] == ["no native id on this record"]


def test_url_shaped_native_id_is_retrievable_by_record_key():
    """The stored native id is a full URL; its slashes/query reshape the native path (permanent 404),
    but the record key is unaffected."""
    store, ids = _archive_store()
    client = _client(store)
    native = client.get(f"/transcripts/teams/{URL_NATIVE_ID}", headers=GATEWAY_HEADERS)
    assert native.status_code == 404, "the native path is expected to remain unable to express this id"

    r = client.get(f"/meetings/{ids['url_native']}/transcript", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["native_meeting_id"] == URL_NATIVE_ID


def test_every_member_of_a_collapse_set_is_addressable_by_record_key():
    """Eight runs behind one recurring link: the native route reaches only the newest; the record key
    reaches each run's own transcript."""
    store, ids = _archive_store()
    client = _client(store)

    native = client.get(f"/transcripts/google_meet/{COLLAPSE_NATIVE_ID}", headers=GATEWAY_HEADERS)
    assert native.status_code == 200
    assert native.json()["id"] == COLLAPSE_WINNER, "native resolution is newest-wins (documented)"

    for mid in ids["collapse"]:
        r = client.get(f"/meetings/{mid}/transcript", headers=GATEWAY_HEADERS)
        assert r.status_code == 200, f"meeting {mid}: {r.text}"
        assert r.json()["id"] == mid
        assert [s["text"] for s in r.json()["segments"]] == [f"run {mid}"]


def test_record_keyed_transcript_is_owner_scoped():
    """Another tenant's key gets 404, never the row (the by-row read shares the by-id authorization)."""
    store, _ = _archive_store()
    r = _client(store).get(f"/meetings/{COLLAPSE_WINNER}/transcript", headers={"x-user-id": str(OTHER)})
    assert r.status_code == 404


def test_record_keyed_transcript_without_identity_is_401():
    store, _ = _archive_store()
    r = _client(store).get(f"/meetings/{COLLAPSE_WINNER}/transcript")
    assert r.status_code == 401


def test_record_keyed_transcript_for_an_unknown_row_is_404():
    store, _ = _archive_store()
    r = _client(store).get("/meetings/99999999/transcript", headers=GATEWAY_HEADERS)
    assert r.status_code == 404


def test_record_key_and_by_id_return_the_same_document():
    """Two spellings of ONE read — the RESTful sub-resource and the pre-existing /transcripts/by-id."""
    store, _ = _archive_store()
    client = _client(store)
    a = client.get(f"/meetings/{COLLAPSE_WINNER}/transcript", headers=GATEWAY_HEADERS)
    b = client.get(f"/transcripts/by-id/{COLLAPSE_WINNER}", headers=GATEWAY_HEADERS)
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_native_and_record_routes_coexist():
    """Adding the literal `transcript` sub-resource must not shadow the native-keyed PATCH/DELETE pair."""
    store, _ = _archive_store()
    r = _client(store).delete(
        f"/meetings/google_meet/{COLLAPSE_NATIVE_ID}", headers=GATEWAY_HEADERS
    )
    assert r.status_code in (200, 409), r.text  # routed to the native handler, not to the new GET


# ── 2. explicit empty state ───────────────────────────────────────────────────────────────────────

def test_empty_transcript_says_so_explicitly():
    """60 of 284 harvested fetches were a bare `200 []`. Now the envelope states it."""
    store, ids = _archive_store()
    r = _client(store).get(f"/meetings/{ids['empty']}/transcript", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"] == []
    assert body["empty"] is True
    assert body["empty_reason"] == "no_segments_stored"
    assert_api_conforms("TranscriptionResponse", body)


def test_non_empty_transcript_carries_empty_false_and_no_reason():
    """`empty` is on EVERY response so a consumer tests one field unconditionally."""
    store, _ = _archive_store()
    body = _client(store).get(
        f"/meetings/{COLLAPSE_WINNER}/transcript", headers=GATEWAY_HEADERS
    ).json()
    assert body["empty"] is False
    assert "empty_reason" not in body


def test_empty_state_is_reported_on_the_native_route_too():
    store, _ = _archive_store()
    body = _client(store).get("/transcripts/zoom/99887766", headers=GATEWAY_HEADERS).json()
    assert body["empty"] is True
    assert body["empty_reason"] == "no_segments_stored"


def test_empty_reason_names_the_known_cause_when_the_record_states_one():
    """Ordered most-specific-first: capture-only > STT fault > failed run > still-running."""
    store = InMemoryTranscriptStore()
    cases = {
        "transcription_disabled": store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id="cap-only-aaa",
            status="completed", data={"transcribe_enabled": False}, segments=[]),
        "transcription_fault": store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id="stt-fault-bbb",
            status="completed", data={"stt_fault": "upstream_5xx"}, segments=[]),
        "meeting_failed": store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id="failed-ccc",
            status="failed", data={"failure_stage": "admission"}, segments=[]),
        "transcription_in_progress": store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id="running-ddd",
            status="active", segments=[]),
    }
    client = _client(store)
    for expected, mid in cases.items():
        body = client.get(f"/meetings/{mid}/transcript", headers=GATEWAY_HEADERS).json()
        assert body["empty"] is True
        assert body["empty_reason"] == expected, f"meeting {mid}"


# ── 3. honest list filters ────────────────────────────────────────────────────────────────────────

def test_unknown_query_param_is_refused_and_names_the_accepted_set():
    """`since` was silently ignored — 200 + the full list. Now it is a 400 the consumer can act on."""
    store, _ = _archive_store()
    r = _client(store).get("/meetings?since=2026-01-01", headers=GATEWAY_HEADERS)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "since" in detail
    for accepted in ("limit", "offset", "status", "platform", "updated_after"):
        assert accepted in detail


def test_several_unknown_params_are_all_named():
    store, _ = _archive_store()
    r = _client(store).get("/meetings?since=x&modified=y", headers=GATEWAY_HEADERS)
    assert r.status_code == 400
    assert "modified" in r.json()["detail"] and "since" in r.json()["detail"]


def test_known_params_still_pass():
    store, _ = _archive_store()
    r = _client(store).get(
        "/meetings?platform=google_meet&status=completed&limit=5&offset=0",
        headers=GATEWAY_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert_api_conforms("MeetingListResponse", r.json())
    assert {m["platform"] for m in r.json()["meetings"]} == {"google_meet"}


def test_updated_after_returns_only_the_delta():
    store, _ = _archive_store()
    client = _client(store)
    full = client.get("/meetings", headers=GATEWAY_HEADERS).json()["meetings"]
    delta = client.get(
        "/meetings?updated_after=2026-05-08T18:00:00Z", headers=GATEWAY_HEADERS
    ).json()["meetings"]
    assert len(delta) < len(full), "the cursor must actually narrow the page"
    # the three records touched after the cursor: the empty zoom row (18:30), and the two later
    # collapse-set runs (2026-05-18, 2026-06-15).
    assert {m["id"] for m in delta} == {12038, 12613, 15496}
    assert all(m["updated_at"] > "2026-05-08T18:00:00Z" for m in delta)


def test_updated_after_is_strict_and_excludes_the_cursor_itself():
    """A consumer resuming from the last row's `updated_at` must not re-read that row forever."""
    store, ids = _archive_store()
    r = _client(store).get(
        "/meetings?updated_after=2026-06-15T09:03:14Z", headers=GATEWAY_HEADERS
    )
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["meetings"]] == []


def test_updated_after_composes_with_the_other_filters():
    store, _ = _archive_store()
    r = _client(store).get(
        "/meetings?updated_after=2026-01-01T00:00:00Z&platform=teams", headers=GATEWAY_HEADERS
    )
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["meetings"]] == [2396]


def test_unparseable_updated_after_is_refused():
    store, _ = _archive_store()
    r = _client(store).get("/meetings?updated_after=last-tuesday", headers=GATEWAY_HEADERS)
    assert r.status_code == 400
    assert "updated_after" in r.json()["detail"]


def test_bots_list_is_deliberately_unchanged():
    """Strict param validation is scoped to GET /meetings. GET /bots backs the shipped dashboard, so
    tightening it here would be a behavior change with no measured defect behind it."""
    store, _ = _archive_store()
    r = _client(store).get("/bots?since=2026-01-01", headers=GATEWAY_HEADERS)
    assert r.status_code == 200
