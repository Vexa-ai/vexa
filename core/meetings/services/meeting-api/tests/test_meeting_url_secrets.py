"""A meeting link is a credential: what meeting-api stores, echoes and logs of it.

Two rules, both from `meeting_url_redaction`:

* **A host-only token is never stored.** Zoom's `zak` starts a meeting as its host; the bot joins as
  a guest. `POST /bots`, the planned-meeting routes and calendar sync drop it on the way in, so it
  reaches no row, no calendar snapshot, no invocation. The attendee passcode stays — a stored link
  must still join.
* **An echoed link is redacted.** The 422s that quote the caller's link keep its host and meeting id
  and mask the passcode.

Each test has its positive control: the same input without the fix carries the secret.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_api import meeting_url_redaction as red
from meeting_api.bot_spawn import build_router
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.calendar_sync import parse_ics, sync_user
from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

SECRET = "test-admin-token"
USER = 7
HEADERS = {"x-user-id": str(USER)}
ZAK = "TEST.ZAK.TOKEN"
PWD = "TESTPASSCODE"
JOIN = f"https://us05web.zoom.us/j/81234567890?pwd={PWD}"
WITH_ZAK = f"{JOIN}&zak={ZAK}"
VECTORS = json.loads((Path(__file__).resolve().parent / "fixtures" / "meeting-url-redaction-vectors.json").read_text())


# ── the shared table ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("section,fn", [("redact_url", red.redact_url),
                                        ("redact_urls_in", red.redact_urls_in),
                                        ("strip_host_tokens", red.strip_host_tokens),
                                        ("strip_host_tokens_in", red.strip_host_tokens_in)])
def test_vectors(section, fn):
    for case in VECTORS[section]:
        assert fn(case["in"]) == case["out"], case["why"]


# ── POST /bots ────────────────────────────────────────────────────────────────────────────────────

def _bots(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    from fastapi import FastAPI

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return TestClient(app), repo, runtime


def test_post_bots_stores_and_dispatches_no_host_token(monkeypatch):
    client, repo, runtime = _bots(monkeypatch)
    r = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "meeting_url": WITH_ZAK})
    assert r.status_code == 201, r.text
    assert ZAK in WITH_ZAK, "positive control: the request carried the host token"
    stored = json.dumps(list(repo._meetings.values()), default=str)
    assert ZAK not in stored and ZAK not in r.text
    assert ZAK not in json.dumps(runtime.specs, default=str), "the bot never receives it"
    assert f"81234567890?pwd={PWD}" in stored, "the attendee passcode still joins"


def test_post_bots_unparseable_link_422_masks_the_passcode(monkeypatch):
    client, _, _ = _bots(monkeypatch)
    r = client.post("/bots", headers=HEADERS,
                    json={"meeting_url": f"https://video.example.org/r/42?pwd={PWD}"})
    assert r.status_code == 422, r.text
    assert PWD not in r.text
    assert "video.example.org/r/42?pwd=***" in r.json()["detail"]


# ── planned meetings ─────────────────────────────────────────────────────────────────────────────

class _Redis:
    async def publish(self, channel, data):
        pass


def test_planned_meeting_create_and_patch_store_no_host_token():
    store = InMemoryTranscriptStore()
    client = TestClient(create_app(store, redis=_Redis()))
    r = client.post("/meetings", json={"title": "x", "meeting_url": WITH_ZAK}, headers=HEADERS)
    assert r.status_code in (200, 201), r.text
    mid = r.json()["id"]
    assert ZAK not in r.text and f"pwd={PWD}" in r.text
    r2 = client.patch(f"/meetings/{mid}", json={"meeting_url": WITH_ZAK.replace("890", "891")},
                      headers=HEADERS)
    assert r2.status_code == 200, r2.text
    assert ZAK not in r2.text
    assert ZAK not in json.dumps(store.__dict__, default=str)


# ── calendar sync ────────────────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def _ics(location: str, description: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:uid-z\r\n"
            "DTSTAMP:20260701T000000Z\r\nDTSTART:20260708T150000Z\r\nSUMMARY:Standup\r\n"
            f"LOCATION:{location}\r\nDESCRIPTION:{description}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


async def test_calendar_sync_stores_no_host_token():
    start = f"https://us05web.zoom.us/s/81234567890?zak={ZAK}"
    parsed = parse_ics(_ics(WITH_ZAK, f"Host link: {start}"), now=NOW)
    (ev,) = parsed["events"]
    assert ev["platform"] == "zoom" and ev["native_meeting_id"] == "81234567890"
    assert ev["meeting_url"] == JOIN, "the join link keeps its passcode and loses the host token"
    assert ZAK not in json.dumps(ev), "nor does the stored iCalendar snapshot carry it"
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parsed, auto_join_default=True)
    (row,) = await store.list_meetings(USER)
    assert ZAK not in json.dumps(row, default=str)
    assert row["data"]["constructed_meeting_url"] == JOIN



@pytest.mark.parametrize("platform,native,url", [
    ("teams", "3741234567890", f"https://teams.evil.example/meet/3741234567890?p={PWD}"),
    ("google_meet", "abc-defg-hij", f"https://meet.evil.example/abc-defg-hij?pwd={PWD}"),
    ("jitsi", "room", f"https://jitsi.corp.example/room?jwt={PWD}"),
])
def test_meeting_host_refusals_carry_no_passcode(monkeypatch, platform, native, url):
    """The host rule's 422s name the host, never the link: the passcode stays out of the refusal."""
    from meeting_api.bot_spawn import meeting_host

    monkeypatch.setattr(meeting_host, "resolver", lambda host: ["10.0.0.7"])
    client, repo, runtime = _bots(monkeypatch)
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": platform, "native_meeting_id": native, "meeting_url": url})
    assert r.status_code == 422, r.text
    assert "meeting_url refused" in r.json()["detail"], "the host rule answered, not an earlier check"
    assert PWD in url, "positive control: the request carried the passcode"
    assert PWD not in r.text
    assert not repo._meetings and not runtime.specs
