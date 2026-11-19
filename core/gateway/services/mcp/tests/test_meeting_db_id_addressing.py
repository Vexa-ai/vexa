"""The tools can address ONE meeting, not just the newest one in a room — fr_b6340167da32b8b6.

Every per-meeting tool took `platform` + `native_meeting_id`, which names a ROOM. A Google Meet
link is the same link every week, so downstream resolves the pair to the caller's NEWEST row on it.
An agent could therefore READ an older meeting (list_meetings returns every row) and then had
nowhere to write back what it learned: `annotate_meeting` wrote to this week's call instead, and
the response looked exactly like success.

`meeting_db_id` is the value every tool ALREADY returns — as `meeting_db_id` on annotate_meeting
and search hits, as `id` on a meeting row. This file asserts the tools now accept it back, that it
takes precedence, and — the part that matters for an agent — that the room-code path is unchanged.

Offline: the fake gateway records the hop, so what is asserted is the URL the shipped forwarding
path actually builds.
"""
from __future__ import annotations

DB_ID = 4242
PLAT, NID = "google_meet", "abc-defg-hij"


def _hop(gateway):
    return gateway.requests[-1]


# ── get_meeting_transcript ───────────────────────────────────────────────────────────────────────

def test_transcript_by_db_id_takes_the_exact_row_route(client, gateway, auth):
    """`/transcripts/by-id/{id}` is owner-scoped and fetches EXACTLY one row; the pair route
    resolves to the newest on the link. Different questions, different routes."""
    client.get(f"/meeting-transcript?meeting_db_id={DB_ID}", headers=auth)
    assert _hop(gateway).url.path == f"/transcripts/by-id/{DB_ID}"


def test_transcript_by_db_id_wins_over_the_room_code(client, gateway, auth):
    client.get(f"/meeting-transcript?platform={PLAT}&native_meeting_id={NID}"
               f"&meeting_db_id={DB_ID}", headers=auth)
    assert _hop(gateway).url.path == f"/transcripts/by-id/{DB_ID}"


def test_transcript_by_room_code_is_unchanged(client, gateway, auth):
    client.get(f"/meeting-transcript?platform={PLAT}&native_meeting_id={NID}", headers=auth)
    assert _hop(gateway).url.path == f"/transcripts/{PLAT}/{NID}"


def test_the_since_index_cursor_still_applies_on_the_db_id_path(client, gateway, auth):
    """The cursor is applied HERE, not at the gateway, so it has to survive the new branch."""
    gateway.routes[("GET", f"/transcripts/by-id/{DB_ID}")] = (
        200, {"segments": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
    )
    body = client.get(f"/meeting-transcript?meeting_db_id={DB_ID}&since_index=2",
                      headers=auth).json()
    assert [s["text"] for s in body["segments"]] == ["c"]
    assert (body["total_segments"], body["next_index"], body["since_index"]) == (3, 3, 2)


# ── annotate_meeting — the write, which is where the wrong row was silent ────────────────────────

def test_annotate_by_db_id_takes_the_row_addressed_route(client, gateway, auth):
    client.post(f"/meeting-annotate?meeting_db_id={DB_ID}", json={"title": "week 1"}, headers=auth)
    hop = _hop(gateway)
    assert hop.url.path == f"/meetings/{DB_ID}/annotate"
    assert hop.method == "POST"


def test_annotate_by_db_id_wins_over_the_room_code(client, gateway, auth):
    client.post(f"/meeting-annotate?platform={PLAT}&native_meeting_id={NID}"
                f"&meeting_db_id={DB_ID}", json={"title": "week 1"}, headers=auth)
    assert _hop(gateway).url.path == f"/meetings/{DB_ID}/annotate"


def test_annotate_by_room_code_is_unchanged(client, gateway, auth):
    client.post(f"/meeting-annotate?platform={PLAT}&native_meeting_id={NID}",
                json={"title": "whichever"}, headers=auth)
    assert _hop(gateway).url.path == f"/meetings/{PLAT}/{NID}/annotate"


def test_the_annotate_echo_names_the_row_that_was_written(client, gateway, auth):
    """The echo is the caller's only evidence of WHICH meeting was written, which is the whole
    reason the pair-addressed write was dangerous."""
    gateway.routes[("POST", f"/meetings/{DB_ID}/annotate")] = (200, {
        "id": DB_ID, "platform": PLAT, "native_meeting_id": NID, "status": "completed",
        "data": {"title": "week 1", "metadata": {"crm_deal": "acme-42"}},
    })
    body = client.post(f"/meeting-annotate?meeting_db_id={DB_ID}",
                       json={"title": "week 1"}, headers=auth).json()
    assert body["meeting_db_id"] == DB_ID
    assert body["title"] == "week 1" and body["metadata"] == {"crm_deal": "acme-42"}
    assert body["platform"] == PLAT and body["native_meeting_id"] == NID


# ── search_transcripts — a filter, not an address ────────────────────────────────────────────────

def test_search_forwards_the_db_id_under_the_rest_spelling(client, gateway, auth):
    """The row id travels as `meeting_db_id` on every TOOL surface precisely so it can never be
    confused with the platform's string id; the REST parameter is `meeting_id`."""
    client.get(f"/transcript-search?q=pricing&meeting_db_id={DB_ID}", headers=auth)
    hop = _hop(gateway)
    assert hop.url.path == "/transcripts/search"
    assert dict(hop.url.params)["meeting_id"] == str(DB_ID)


def test_search_can_still_filter_by_room_code(client, gateway, auth):
    client.get(f"/transcript-search?q=pricing&native_meeting_id={NID}", headers=auth)
    params = dict(_hop(gateway).url.params)
    assert params["native_meeting_id"] == NID and "meeting_id" not in params


def test_search_sends_neither_when_neither_is_asked_for(client, gateway, auth):
    client.get("/transcript-search?q=pricing", headers=auth)
    params = dict(_hop(gateway).url.params)
    assert "meeting_id" not in params and "native_meeting_id" not in params


# ── the refusals an agent has to be able to act on ───────────────────────────────────────────────

def test_the_missing_id_refusal_now_names_the_db_id_too(client, auth):
    """A caller holding a row it just read should be told the shortest way in."""
    for path in ("/meeting-transcript", "/meeting-annotate"):
        r = (client.get(path, headers=auth) if "transcript" in path
             else client.post(path, json={"title": "x"}, headers=auth))
        assert r.status_code == 422
        assert "meeting_db_id" in str(r.json()["detail"])


def test_a_non_integer_db_id_is_refused_by_the_tool(client, auth):
    """Not forwarded as a path segment for the gateway to choke on two hops away."""
    assert client.get("/meeting-transcript?meeting_db_id=not-a-number",
                      headers=auth).status_code == 422
