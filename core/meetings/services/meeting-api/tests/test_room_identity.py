"""room_identity — the room-device classifier on the READ side (Vexa Rooms rung 1).

Asserts:
  • the embedded table has NOT drifted from the cross-language source of truth
    (meetings/contracts/room-identity/room-patterns.json). This is the gate that keeps the Python
    read path and the TypeScript producer (services/bot/src/room-identity.ts) agreeing — change
    the JSON and both languages go red until both follow.
  • room kits classify as `room`, naming WHICH pattern fired;
  • the names that must stay `person` stay `person` — `Roomradar` is a live Vexa customer, which
    is why the numbered-room pattern demands digits;
  • the documented blind spot is asserted rather than hidden: a room kit named `Steve Jobs` reads
    `person`, because nothing in the data distinguishes it;
  • a refused / provisional label is `unknown`, never `person`;
  • VEXA_ROOM_PATTERNS replaces the table, `"+"` extends it, and every malformed shape fails SAFE.

Autonomous — no docker, no network, no database.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_api.collector.room_identity import (
    DEFAULT_ROOM_PATTERNS,
    ROOM_PATTERNS_ENV,
    compile_patterns,
    matched_pattern,
    speaker_kind,
)


def _ssot() -> dict:
    rel = Path("meetings") / "contracts" / "room-identity" / "room-patterns.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
        candidate = parent / "core" / rel
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise AssertionError(f"could not locate the room-identity source of truth ({rel})")


def test_embedded_table_matches_the_cross_language_ssot():
    """A1/A6 — the Python table IS the JSON table. Drift here means the bot and the API would give
    two different answers about the same speaker, silently."""
    from_file = [(p["id"], p["regex"]) for p in _ssot()["patterns"]]
    assert list(DEFAULT_ROOM_PATTERNS) == from_file


def test_every_pattern_carries_its_evidence():
    """A pattern with no stated reason is a guess somebody will be unable to audit later."""
    for p in _ssot()["patterns"]:
        assert len(p.get("why", "").strip()) > 20, p["id"]


@pytest.mark.parametrize(
    "name,pattern_id",
    [
        ("Amsterdam — Room 2", "numbered-room"),
        ("Meeting Room", "room-word"),
        ("HQ Conference Room", "room-word"),
        ("Boardroom", "room-word"),
        ("Rm-14", "numbered-room"),
        ("Utrecht (Room)", "room-suffix"),
        ("devices/AAkZ4Wc", "meet-device-resource"),
        ("Microsoft Teams Rooms", "teams-room"),
        ("Surface Hub 2S", "teams-room"),
        ("Zoom Room", "zoom-room"),
        ("Series One Board 65", "google-meet-hardware"),
        ("Logitech Rally Bar", "vendor-room-kit"),
        ("Poly Studio X50", "vendor-room-kit"),
        ("Neat Board", "vendor-room-kit"),
        ("Cisco Room Kit Pro", "vendor-room-kit"),
        ("Vergaderruimte Noord", "room-word-non-english"),
        ("Salle de réunion 1", "room-word-non-english"),
    ],
)
def test_room_devices_are_detected_and_say_which_pattern_fired(name, pattern_id):
    assert speaker_kind(name) == "room"
    assert matched_pattern(name) == pattern_id


@pytest.mark.parametrize(
    "name",
    [
        "Robin Dirksen",
        "Dmitry Grankin",
        # A live Vexa customer. A bare `\\broom\\b` would have called this account a room device —
        # which is why the numbered-room pattern requires digits.
        "Roomradar",
        "Zoom Video Communications",
        "Neatly Ltd",
        "Broom Hilda",
        "Poly Studios",
    ],
)
def test_people_stay_people(name):
    assert speaker_kind(name) == "person", matched_pattern(name)


def test_the_documented_blind_spot_is_asserted_not_hidden():
    """A REAL room kit, met on a customer call on 2026-09-15: a Meet kit whose device-name field
    somebody had filled in with `Steve Jobs`. Eighteen transcript segments of a Dutch engineer
    speaking were attributed to it. It reads `person` and always will — nothing in the data
    distinguishes a room named after a human from a human. The remedy is one admin-console edit on
    the customer's side, not a cleverer regex. This test exists so that stays visible."""
    assert speaker_kind("Steve Jobs") == "person"


def test_a_vendor_word_inside_a_company_name_is_the_known_false_positive():
    """The opposite cost of matching model names. Stated, not hidden — an operator overrides it."""
    assert speaker_kind("Roommate Ventures") == "room"


@pytest.mark.parametrize(
    "name",
    ["", "   ", None, "Speaker", "Speaker A", "Speaker AB", "seg_17", "ch-3", "ch-3:2"],
)
def test_a_refusal_is_unknown_never_person(name):
    """The binder publishes a blank rather than guessing. Dressing that as `person` would be the
    same failed claim the blanking rule exists to stop."""
    assert speaker_kind(name) == "unknown"


def test_env_override_replaces_the_table():
    pats = compile_patterns({ROOM_PATTERNS_ENV: json.dumps(["^HQ-"])})
    assert speaker_kind("HQ-Oslo", pats) == "room"
    assert speaker_kind("Meeting Room", pats) == "person"
    assert [pid for pid, _ in pats] == ["env:0"]


def test_env_override_can_extend_instead_of_replace():
    pats = compile_patterns({ROOM_PATTERNS_ENV: json.dumps(["+", "^HQ-"])})
    assert speaker_kind("HQ-Oslo", pats) == "room"
    assert speaker_kind("Meeting Room", pats) == "room"
    assert len(pats) == len(DEFAULT_ROOM_PATTERNS) + 1


@pytest.mark.parametrize(
    "raw",
    ["meeting room, board room", '{"patterns":["x"]}', "[1,2,3]", "[]", '["+"]'],
)
def test_a_malformed_override_fails_safe(raw):
    """A bad env var must never take the read path down, and must never silently disable the
    defaults either — it is ignored and the shipped table stands."""
    pats = compile_patterns({ROOM_PATTERNS_ENV: raw})
    assert [pid for pid, _ in pats] == [pid for pid, _ in DEFAULT_ROOM_PATTERNS]
    assert speaker_kind("Meeting Room", pats) == "room"


def test_one_uncompilable_pattern_is_dropped_and_the_rest_survive():
    pats = compile_patterns({ROOM_PATTERNS_ENV: json.dumps(["^HQ-", "([unclosed"])})
    assert [pid for pid, _ in pats] == ["env:0"]
    assert speaker_kind("HQ-Oslo", pats) == "room"


# ── the field reaches the wire: GET /transcripts carries speaker_kind ──────────────────────────
def test_the_served_transcript_carries_speaker_kind():
    """A5 — derived on READ from `transcriptions.speaker`, so it needs no column and no migration,
    and rows written before this shipped are classified too. Attribution is untouched: the room is
    still one speaker under its own display name; what changed is that the segment says the name
    belongs to a device."""
    from fastapi.testclient import TestClient

    from meeting_api.collector import create_app
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    class _Redis:
        async def publish(self, channel, data):  # pragma: no cover - not exercised here
            return None

    store = InMemoryTranscriptStore()
    client = TestClient(create_app(store, redis=_Redis()))
    store.seed_meeting(
        user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij",
        segments=[
            {"segment_id": "s1", "speaker": "Amsterdam — Room 2", "text": "we agreed on that",
             "start": 1.0, "end": 2.0, "completed": True},
            {"segment_id": "s2", "speaker": "Alice", "text": "yes",
             "start": 3.0, "end": 4.0, "completed": True},
            {"segment_id": "s3", "speaker": "", "text": "mm",
             "start": 5.0, "end": 6.0, "completed": True},
        ],
    )

    body = client.get("/transcripts/google_meet/abc-defg-hij", headers={"x-user-id": "7"}).json()
    kinds = {s.get("segment_id"): s.get("speaker_kind") for s in body["segments"]}

    assert kinds == {"s1": "room", "s2": "person", "s3": "unknown"}
