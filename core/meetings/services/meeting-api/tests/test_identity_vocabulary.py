"""THE GATE, response side — no route may EMIT a retired identity name.

meeting-api owns the response shapes the MCP tools forward verbatim, so this is where the
regression actually lived: ``search_transcripts`` selected ``t.meeting_id AS meeting_id``, putting
the INTEGER row id under the name three other tools use for the NATIVE STRING id. Feeding that
tool's own output into ``get_meeting_transcript`` produced ``404 … and ID 1``.

It was the second occurrence of that defect class in one day, by the same author, with the rule
written down in between — so it is asserted here rather than remembered.

The vocabulary MIRRORS ``vexa_mcp.identity`` (the input-side gate lives there, against the MCP
tool schemas). The two services have separate dependency trees and cannot import each other, so
this is a deliberate mirror in the same style as the ``sessions/models.py`` ↔ admin-api schema
mirror. If you change one, change both — and the constants below are tiny precisely so that stays
cheap.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

#: MIRROR of vexa_mcp.identity.CANONICAL_IDENTITY — concept -> the kind of value it always holds.
CANONICAL_IDENTITY = {"platform": str, "native_meeting_id": str, "meeting_db_id": int}
#: MIRROR of vexa_mcp.identity.DEPRECATED_ALIASES — retired name -> replacement. Never emitted.
DEPRECATED_ALIASES = {"meeting_id": "native_meeting_id", "meeting_platform": "platform"}

USER = 7
H = {"x-user-id": str(USER)}
PLAT, NID = "google_meet", "abc-defg-hij"


class _NullRedis:
    async def publish(self, channel, data):
        return None


def emitted_violations(where: str, payload, *, path: str = "") -> list[str]:
    """Every retired name, and every canonical name carrying the wrong kind of value."""
    out: list[str] = []
    if isinstance(payload, list):
        for item in payload[:5]:
            out += emitted_violations(where, item, path=f"{path}[]")
        return out
    if not isinstance(payload, dict):
        return out
    for key, value in payload.items():
        here = f"{path}.{key}" if path else key
        if key in DEPRECATED_ALIASES:
            out.append(f"{where}: emits `{here}` (retired; use `{DEPRECATED_ALIASES[key]}`)")
        elif key in CANONICAL_IDENTITY and value is not None:
            want = CANONICAL_IDENTITY[key]
            if want is int and (not isinstance(value, int) or isinstance(value, bool)):
                out.append(f"{where}: `{here}` should be an int, got {value!r}")
            if want is str and not isinstance(value, str):
                out.append(f"{where}: `{here}` should be a string, got {value!r}")
        out += emitted_violations(where, value, path=here)
    return out


def _client():
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=USER, platform=PLAT, native_meeting_id=NID, meeting_id=1, status="completed",
        segments=[{"segment_id": "s1", "start": 0.0, "end": 2.0,
                   "text": "take the back of the panel", "speaker": "Dmitriy", "language": "en"}],
    )
    return TestClient(create_app(store, redis=_NullRedis()))


# ---- the gate ---------------------------------------------------------------------------

def test_no_route_emits_a_retired_identity_name():
    """Drives every identity-bearing read route and inspects what it actually returns."""
    client = _client()
    checked = 0
    for label, path, params in [
        ("GET /meetings", "/meetings", {}),
        ("GET /transcripts/search", "/transcripts/search", {"q": "panel"}),
        ("GET /transcripts/{platform}/{native}", f"/transcripts/{PLAT}/{NID}", {}),
        ("GET /transcripts/by-id", "/transcripts/by-id/1", {}),
    ]:
        r = client.get(path, params=params, headers=H)
        if r.status_code != 200:
            continue
        checked += 1
        violations = emitted_violations(label, r.json())
        assert not violations, (
            "A route EMITS a retired identity name — the defect that shipped twice:\n  - "
            + "\n  - ".join(violations)
            + "\n\nOutputs are what a caller builds the NEXT call from, so an ambiguous output "
              "name is the bug: it turns 'feed one tool's output into the next' into a 404."
        )
    assert checked >= 3, "too few routes exercised for this gate to mean anything"


def test_search_hits_carry_the_names_the_next_call_needs():
    """The composability promise, made concrete: a search hit must be feedable straight into the
    transcript route without renaming anything."""
    client = _client()
    hits = client.get("/transcripts/search", params={"q": "panel"}, headers=H).json()["hits"]
    assert hits, "fixture produced no hits; the rest of this test would be vacuous"
    hit = hits[0]
    assert "platform" in hit and "native_meeting_id" in hit
    # The actual round trip — this is what 404'd before.
    r = client.get(f"/transcripts/{hit['platform']}/{hit['native_meeting_id']}", headers=H)
    assert r.status_code == 200, (
        f"a search hit could not be fed into the transcript route: {r.status_code} {r.text[:200]}"
    )


def test_the_integer_row_id_is_never_called_meeting_id():
    """Specifically pins the regression: the int row id travels as `meeting_db_id`, never under
    the name that means the platform's string id elsewhere."""
    client = _client()
    hit = client.get("/transcripts/search", params={"q": "panel"}, headers=H).json()["hits"][0]
    assert "meeting_id" not in hit
    assert isinstance(hit.get("meeting_db_id"), int)
    assert isinstance(hit.get("native_meeting_id"), str)


# ---- guards on the checker itself --------------------------------------------------------

def test_the_checker_catches_the_exact_regression():
    """A gate that only ever passes proves nothing. This is the shape that shipped."""
    assert emitted_violations("x", {"hits": [{"meeting_id": 1}]})
    assert emitted_violations("x", {"meeting_platform": "zoom"})
    assert emitted_violations("x", {"native_meeting_id": 7})      # right name, wrong kind
    assert emitted_violations("x", {"meeting_db_id": "abc-defg"})  # right name, wrong kind
    assert emitted_violations("x", [{"nested": {"meeting_id": 2}}])


def test_the_checker_passes_a_clean_payload():
    assert emitted_violations("x", {
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "meeting_db_id": 1, "speaker": "Dmitriy",
    }) == []
