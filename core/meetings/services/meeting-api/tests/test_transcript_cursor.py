"""#1219 · the transcript read is INCREMENTAL, and an unsupported parameter is REFUSED.

Two defects, one class — the endpoint answered 200 to requests it did not honour:

  * it returned the ENTIRE transcript on every poll, so following one 2h meeting at a 5s poll cost
    ~172MB to learn ~250KB of new text (quadratic: payload and poll count both grow with length);
  * `?since` / `?after` / `?start_time` / `?limit=2` / `?offset=2` were all accepted and dropped —
    200 with the full payload, no 400, no typed refusal.

The load-bearing test here is `test_cursor_poll_union_matches_full_poll`: a cursor that silently
drops a segment is WORSE than no cursor, so the property under test is that a follower polling with
the cursor ends up holding exactly what a full read returns — across new segments, in-place
revisions (a draft rewritten as its confirmation) and retractions.

Offline: the in-memory fake + TestClient, no docker, no DB, no meeting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.cursor import RETRACTION_TOMBSTONE_TTL_SEC, iso
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
NATIVE = "abc-defg-hij"
GATEWAY_HEADERS = {"x-user-id": str(USER)}


def _iso_ago(seconds: float) -> str:
    return iso(datetime.now(timezone.utc) - timedelta(seconds=seconds))


def _seg(sid: str, start: float, text: str, *, ago: float, completed: bool = True) -> dict:
    """One segment as ingest stores it — `updated_at` is the change-stamp the cursor filters on."""
    return {
        "segment_id": sid, "start": start, "end": start + 1.0, "text": text,
        "language": "en", "speaker": "spk-A", "completed": completed,
        "updated_at": _iso_ago(ago),
    }


def _seeded(segments=None):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id=NATIVE, status="active",
        constructed_meeting_url=f"https://meet.google.com/{NATIVE}",
        segments=segments if segments is not None else [_seg("s1", 1.0, "one", ago=600)],
    )
    return store, mid, TestClient(create_app(store, redis=None))


def _get(client, **params):
    r = client.get(f"/transcripts/google_meet/{NATIVE}", headers=GATEWAY_HEADERS, params=params)
    return r


# ── 1. the refusal: a parameter that changes nothing must not answer 200 ──────────────────────────

def test_limit_is_refused_rather_than_silently_dropped():
    """The measured symptom: `?limit=2` returned 200 with the FULL payload (11,095 bytes)."""
    _, _, client = _seeded()
    r = _get(client, limit=2)
    assert r.status_code == 400, r.text
    assert "limit" in r.json()["detail"]


def test_every_silently_dropped_parameter_from_the_report_is_now_refused():
    """`after`, `start_time`, `offset` — each measured as 200-with-full-payload on 2026-08-18."""
    _, _, client = _seeded()
    for param in ("after", "start_time", "offset", "after_segment_id"):
        r = _get(client, **{param: "whatever"})
        assert r.status_code == 400, f"{param}: {r.status_code} {r.text}"
        detail = r.json()["detail"]
        assert param in detail, detail
        # The refusal must SAY what the endpoint does accept — the thing Stefan Huber asked for on
        # #892: "a validation error instead of silent drop would save a lot of debugging."
        assert "since" in detail, detail


def test_refusal_names_every_offending_parameter_at_once():
    _, _, client = _seeded()
    r = _get(client, after="x", offset="2")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "after" in detail and "offset" in detail


def test_unparsable_since_is_refused():
    _, _, client = _seeded()
    r = _get(client, since="last tuesday")
    assert r.status_code == 400, r.text
    assert "since" in r.json()["detail"]


def test_error_body_matches_the_house_envelope():
    """Same `{"detail": ...}` shape every other handler emits (test_api_agility's envelope rule)."""
    _, _, client = _seeded()
    body = _get(client, limit=2).json()
    assert isinstance(body, dict) and isinstance(body.get("detail"), str)


def test_supported_cursor_still_answers_200():
    _, _, client = _seeded()
    assert _get(client, since=_iso_ago(3600)).status_code == 200


# ── 2. the cursor actually cuts the payload ───────────────────────────────────────────────────────

def test_since_returns_only_what_changed():
    store, _, client = _seeded([
        _seg("s1", 1.0, "old one", ago=600),
        _seg("s2", 2.0, "old two", ago=600),
        _seg("s3", 3.0, "fresh", ago=1),
    ])
    full = _get(client).json()
    assert len(full["segments"]) == 3

    incremental = _get(client, since=_iso_ago(30)).json()
    assert [s["segment_id"] for s in incremental["segments"]] == ["s3"]
    # The point of the exercise: the incremental body is materially smaller.
    assert len(_get(client, since=_iso_ago(30)).content) < len(_get(client).content)


def test_every_response_carries_the_next_watermark():
    """Present on a FULL read too — that is how a follower bootstraps into incremental polling."""
    _, _, client = _seeded()
    body = _get(client).json()
    assert body["next_since"].endswith("Z")
    stamp = datetime.fromisoformat(body["next_since"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    # It LAGS now (safety), and not by much.
    assert stamp < now
    assert (now - stamp) < timedelta(seconds=60)


def test_segments_carry_their_change_stamp():
    _, _, client = _seeded()
    assert _get(client).json()["segments"][0]["updated_at"].endswith("Z")


# ── 3. THE PROPERTY: a follower's union == a full read ────────────────────────────────────────────

def _follow(body, held: dict) -> dict:
    """A follower's merge step: upsert by `segment_id`, drop what was retracted. Exactly what every
    Vexa client already does (the terminal, the copilot worker, the dashboard all upsert by id)."""
    for seg in body["segments"]:
        held[seg["segment_id"]] = seg
    for sid in body.get("retracted_segment_ids", []):
        held.pop(sid, None)
    return held


def _texts(segments) -> list:
    return sorted((s["segment_id"], s["text"]) for s in segments)


async def test_cursor_poll_union_matches_full_poll():
    """The live positive control, offline: poll with the cursor across a growing + REVISED transcript
    and the union must equal what a full poll returns — same ids, same text.

    The revision leg is what kills an `after_segment_id` cursor: `s2` is first served as a draft and
    later REWRITTEN under the same id as its confirmation. An append-pointer cursor would hand the
    follower the draft and never the confirmation."""
    store, mid, client = _seeded([_seg("s1", 1.0, "one", ago=600)])

    held: dict = {}
    held = _follow(_get(client).json(), held)          # poll 1 — full bootstrap
    cursor = _get(client).json()["next_since"]

    # …a new segment arrives, and s1 is REVISED in place (draft → confirmation).
    await store.append_segment(mid, _seg("s2", 2.0, "two", ago=0, completed=False))
    await store.append_segment(mid, _seg("s1", 1.0, "one, corrected", ago=0))

    held = _follow(_get(client, since=cursor).json(), held)   # poll 2 — incremental
    cursor = _get(client, since=cursor).json()["next_since"]

    # …the draft is confirmed under its own id.
    await store.append_segment(mid, _seg("s2", 2.0, "two, confirmed", ago=0, completed=True))
    held = _follow(_get(client, since=cursor).json(), held)   # poll 3 — incremental

    full = _get(client).json()
    assert _texts(held.values()) == _texts(full["segments"])
    assert _texts(held.values()) == [("s1", "one, corrected"), ("s2", "two, confirmed")]


async def test_retraction_reaches_the_cursor_reader():
    """A deletion is the one change a changed-since cursor cannot express as data. Without the
    tombstone the follower keeps serving a draft the full transcript no longer contains — the union
    property breaks and the cursor is silently lying."""
    store, mid, client = _seeded([_seg("s1", 1.0, "one", ago=600)])
    await store.append_segment(mid, _seg("draft:p0", 2.0, "half a sen", ago=0, completed=False))

    held = _follow(_get(client).json(), held={})
    assert "draft:p0" in held
    cursor = _get(client).json()["next_since"]

    await store.delete_segments(mid, ["draft:p0"])

    body = _get(client, since=cursor).json()
    assert body["retracted_segment_ids"] == ["draft:p0"]
    held = _follow(body, held)

    full = _get(client).json()
    assert _texts(held.values()) == _texts(full["segments"])
    assert "draft:p0" not in held


def test_retraction_list_only_rides_on_a_cursor_read():
    """A full read is self-consistent — its `segments` ARE the truth, so there is nothing to retract
    and no unbounded tombstone list to ship."""
    _, _, client = _seeded()
    assert "retracted_segment_ids" not in _get(client).json()
    assert "retracted_segment_ids" in _get(client, since=_iso_ago(10)).json()


# ── 4. failing open — the cursor never silently loses a segment ───────────────────────────────────

def test_segment_without_a_change_stamp_is_always_returned():
    """A legacy producer's unstamped segment must never be the one the follower loses."""
    unstamped = {"segment_id": "legacy", "start": 9.0, "end": 10.0, "text": "no stamp",
                 "language": "en", "completed": True}
    _, _, client = _seeded([_seg("s1", 1.0, "one", ago=600), unstamped])
    ids = [s["segment_id"] for s in _get(client, since=_iso_ago(1)).json()["segments"]]
    assert "legacy" in ids


def test_stale_cursor_degrades_to_a_full_read_not_a_wrong_one():
    """Past the retraction-tombstone window a retraction may have expired unseen, so the incremental
    answer cannot be shown to be complete. Serve everything and SAY so."""
    store, _, client = _seeded([_seg("s1", 1.0, "one", ago=600), _seg("s2", 2.0, "two", ago=600)])
    body = _get(client, since=_iso_ago(RETRACTION_TOMBSTONE_TTL_SEC + 60)).json()
    assert body["resynced"] is True
    assert len(body["segments"]) == 2, "a cursor we cannot honour completely must return everything"


def test_fresh_cursor_is_not_flagged_as_resynced():
    _, _, client = _seeded()
    assert _get(client, since=_iso_ago(10)).json()["resynced"] is False


# ── 5. the by-id sibling reads the same way ───────────────────────────────────────────────────────

def test_by_id_path_honours_the_cursor_and_refuses_the_rest():
    store, mid, client = _seeded([_seg("s1", 1.0, "old", ago=600), _seg("s2", 2.0, "new", ago=1)])
    r = client.get(f"/transcripts/by-id/{mid}", headers=GATEWAY_HEADERS, params={"since": _iso_ago(30)})
    assert r.status_code == 200, r.text
    assert [s["segment_id"] for s in r.json()["segments"]] == ["s2"]

    bad = client.get(f"/transcripts/by-id/{mid}", headers=GATEWAY_HEADERS, params={"limit": 2})
    assert bad.status_code == 400


def test_cursor_does_not_bypass_ownership():
    """The cursor is a read filter, never an authorization path — another tenant still gets 404."""
    _, _, client = _seeded()
    r = client.get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": "999"},
                   params={"since": _iso_ago(10)})
    assert r.status_code == 404
