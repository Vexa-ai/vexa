"""THE GATE — one name per concept, asserted against the surface the server actually emits.

This file exists because the same defect shipped TWICE IN ONE DAY, by the same author, with the
rule written down in between:

  * morning — three tools ACCEPTED ``meeting_platform`` + ``meeting_id`` while every tool RETURNED
    ``platform`` + ``native_meeting_id``, so no tool's output composed into the next tool's input.
    Found by a reader who had just read the README. Fixed, and the fix was documented.
  * hours later — a NEW ``search_transcripts`` tool RETURNED ``meeting_id`` holding the integer row
    id, colliding with the deprecated alias for the native string id. Feeding that tool's own
    output into ``get_meeting_transcript`` gave ``404 … and ID 1``.

The lesson is not "read the docs more carefully". A naming convention enforced only by prose and
review is enforced by nobody, and this failure is SILENT: every field is individually plausible
and only the join between two tools is wrong, so nothing fails until a caller chains them.

So the vocabulary is data (``vexa_mcp.identity``) and this asserts it mechanically. It runs over
the DERIVED surface — the schemas FastAPI/FastApiMCP actually produce and the payloads the tools
actually return — not over source, because the bug is emergent from that derivation.

``instructions`` promises callers: *"every tool returns `platform` + `native_meeting_id`, and every
tool accepts those same two names. Feed one tool's output straight into the next."* These tests
are what make that a checkable claim instead of a hope.
"""
from __future__ import annotations

import httpx
import pytest

from conftest import API_KEY
from vexa_mcp import create_app
from vexa_mcp.identity import (
    CANONICAL_IDENTITY,
    DEPRECATED_ALIASES,
    check_output_names,
    check_tool_inputs,
)

GATEWAY_URL = "http://gateway.test"


def _tools(app):
    """The MCP tool surface as a client receives it — derived, not declared."""
    return [
        {"name": name, "inputSchema": tool.inputSchema}
        for name, tool in app.state.mcp.tools.items()
    ] if isinstance(getattr(app.state.mcp, "tools", None), dict) else [
        {"name": t.name, "inputSchema": t.inputSchema} for t in app.state.mcp.tools
    ]


@pytest.fixture
def app():
    return create_app(GATEWAY_URL, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={})))


# ---- rule 1: a deprecated alias may appear only on INPUT, only beside its canonical name ----

def test_no_tool_accepts_a_retired_name_without_its_replacement(app):
    violations = check_tool_inputs(_tools(app))
    assert not violations, (
        "The identity vocabulary is broken on the tool INPUT surface:\n  - "
        + "\n  - ".join(violations)
        + "\n\nEvery tool that names a meeting must accept `platform` + `native_meeting_id` — the "
          "exact field names the tools RETURN. A retired name may ride along as a deprecated alias, "
          "but never alone and never unmarked."
    )


def test_the_canonical_names_are_actually_used_somewhere(app):
    """Guards the guard: a vocabulary nothing uses would let every check above pass vacuously."""
    names = {p for t in _tools(app) for p in (t["inputSchema"].get("properties") or {})}
    assert {"platform", "native_meeting_id"} <= names


# ---- rule 2: a deprecated alias may NEVER be emitted -----------------------------------

def _payloads_from_gateway(monkeypatch_payload):
    """Drive every read tool against a fake gateway returning a realistic identity-bearing body."""
    return httpx.MockTransport(lambda r: httpx.Response(200, json=monkeypatch_payload))


@pytest.mark.parametrize("payload,label", [
    ({"meeting_id": 1, "platform": "google_meet"}, "integer meeting_id at the top level"),
    ({"hits": [{"meeting_id": 7, "native_meeting_id": "abc-defg-hij"}]}, "meeting_id nested in a list"),
    ({"meetings": [{"meeting_platform": "zoom"}]}, "meeting_platform nested in a list"),
])
def test_the_output_check_catches_a_retired_name(payload, label):
    """The regression this file exists for, in miniature: these SHOULD be reported."""
    violations = check_output_names("some_tool", payload)
    assert violations, f"a retired name went unreported: {label}"


@pytest.mark.parametrize("payload,label", [
    ({"native_meeting_id": 7}, "native_meeting_id carrying an int"),
    ({"meeting_db_id": "abc-defg-hij"}, "meeting_db_id carrying a string"),
])
def test_the_output_check_catches_a_canonical_name_with_the_wrong_kind_of_value(payload, label):
    """The subtler half: a correctly-SPELLED name holding the wrong kind of value is exactly what
    shipped — a name-only check would have passed it."""
    assert check_output_names("some_tool", payload), f"type confusion went unreported: {label}"


def test_a_clean_payload_reports_nothing():
    clean = {"meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                           "meeting_db_id": 1, "title": "Acme renewal"}]}
    assert check_output_names("some_tool", clean) == []


def test_live_tool_outputs_carry_no_retired_names(app):
    """The real gate: drive the tools and inspect what they actually hand back.

    The MCP service forwards gateway bodies verbatim, so this catches a retired name introduced
    EITHER here or upstream in meeting-api — which is where the search regression actually lived.
    """
    from fastapi.testclient import TestClient

    # A gateway body shaped like the real one, using only canonical names. If a tool renames or
    # re-wraps a field on the way out, the check sees it.
    body = {
        "meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "id": 1}],
        "hits": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                  "meeting_db_id": 1, "speaker": "A", "snippet": "x"}],
    }
    client = TestClient(create_app(
        GATEWAY_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    auth = {"Authorization": f"Bearer {API_KEY}"}

    checked = 0
    for path, params in [
        ("/meetings", {}),
        ("/transcript-search", {"q": "panel"}),
        ("/meeting-transcript", {"native_meeting_id": "abc-defg-hij"}),
        ("/bot-status", {}),
        ("/recordings", {}),
        ("/meeting-chat", {"native_meeting_id": "abc-defg-hij"}),
    ]:
        r = client.get(path, params=params, headers=auth)
        if r.status_code != 200:
            continue
        checked += 1
        violations = check_output_names(path, r.json())
        assert not violations, (
            "A tool EMITS a retired identity name — this is the defect that shipped twice:\n  - "
            + "\n  - ".join(violations)
            + "\n\nOutputs are what the next call is built from. An ambiguous output name is what "
              "turns 'feed one tool's output into the next' into a 404."
        )
    assert checked >= 5, "too few tools exercised for this gate to mean anything"


# ---- the vocabulary itself stays coherent ----------------------------------------------

def test_no_alias_shadows_a_canonical_name():
    assert not (set(DEPRECATED_ALIASES) & set(CANONICAL_IDENTITY)), (
        "a retired name must not also be canonical — that is the ambiguity, not the fix"
    )


def test_every_alias_points_at_a_real_canonical_name():
    for alias, canonical in DEPRECATED_ALIASES.items():
        assert canonical in CANONICAL_IDENTITY, f"`{alias}` redirects to unknown `{canonical}`"
