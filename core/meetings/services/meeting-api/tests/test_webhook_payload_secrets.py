"""What a DELIVERED webhook body may carry out of the service.

Sibling of ``test_response_secret_projection.py``, which pins the same question on the READ edge
(``GET /meetings``, ``GET /meetings/{id}``, the transcript paths). This file pins the WRITE edge: the
envelope POSTed to a subscriber's endpoint.

The read edge is the stricter test of the two in one sense — it serves people who are not the owner —
but the write edge crosses further. A delivered body leaves our trust boundary completely: it is
POSTed to an operator-configured system endpoint, or to an arbitrary URL a user typed into their
settings, and lands in a request log we do not run and cannot revoke. ``meeting.data`` is an open
multi-producer blob, so the standing rule is that the outbound body is at least as strict as a
response to a non-owner: what one edge withholds, both withhold.

Three classes reached this edge until now, all of them keys the read edge already drops:

* ``share_grants`` — per-link ``secret_hash`` plus the allow-list and expiry: the credential half of
  the share machinery;
* ``transcript_viewers`` — the reader roster, i.e. OTHER PEOPLE's identities, which the endpoint's
  operator has no claim to;
* ``auth_userdata_path`` — the S3 pointer to a live authenticated browser session's cookies.

Plus the general case the last two years of this blob argue for: a credential-SHAPED key a future
producer stamps into ``data`` must be withheld by DEFAULT. ``webhook_secret`` reached a second party
for months because a new key arriving in a shared blob is nobody's review item.

Run: cd core/meetings/services/meeting-api && python -m pytest tests/test_webhook_payload_secrets.py -q
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.collector.projection import (
    OWNER_ONLY_KEYS,
    RESPONSE_OMIT_KEYS,
    SENSITIVE_OMIT_KEYS,
)
from meeting_api.webhooks import WebhookSink, build_envelope, clean_meeting_data
from meeting_api.webhooks.delivery import _INTERNAL_DATA_KEYS

URL = "https://hooks.example.com/vexa"
SECRET = "whsec_payload_secret"
_PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731 — keep the SSRF guard off DNS

# Every value below is a distinctive marker string: the assertions search the RAW delivered bytes for
# it, so a leak through a nested structure, a re-serialization or a future envelope change is caught
# just as well as a top-level key that survived the filter.
SECRET_VALUES = {
    "webhook_secret": "whsec_LEAK_the_signing_key",
    "share_grants": [{"secret_hash": "LEAKHASH_deadbeef", "allow": ["x@example.com"]}],
    "auth_userdata_path": "s3://vexa-sessions/LEAKPATH/userdata.tar",
    "transcript_viewers": [{"user_id": 4242, "email": "LEAKVIEWER@example.com"}],
    # Credential-SHAPED and not on any explicit list — the default-deny case.
    "zoom_api_key": "LEAKKEY_zoom_9f8e7d",
    "session_token": "LEAKTOKEN_vnc_session",
}
CONTENT = {
    "name": "Weekly sync",
    "notes": "kept — this is meeting content",
    "completion_reason": "stopped",
    "scheduled_at": "2026-09-17T10:00:00Z",
}


def _assert_clean(blob: str, *, where: str) -> None:
    """No marker value anywhere in ``blob`` (the serialized delivery)."""
    for key, value in SECRET_VALUES.items():
        rendered = value if isinstance(value, str) else json.dumps(value)
        for marker in _markers(rendered):
            assert marker not in blob, f"{where}: {key} leaked into the delivered payload"
        assert f'"{key}"' not in blob, f"{where}: the key {key} itself rode the payload"


def _markers(rendered: str) -> List[str]:
    return [tok for tok in rendered.replace('"', " ").replace(",", " ").split() if "LEAK" in tok]


# ── the filter itself ────────────────────────────────────────────────────────────────────────────


def test_clean_meeting_data_drops_every_key_a_non_owner_response_drops():
    """The standing invariant, stated as an assertion: the write edge ⊇ the read edge's omissions.

    The pre-existing pin in ``test_response_secret_projection.py`` runs one way only (every
    credential key the delivery path knows about is also omitted from responses). This is the other
    direction, and it is the one that was false: ``share_grants``, ``transcript_viewers`` and
    ``auth_userdata_path`` were dropped from responses and delivered over the wire.
    """
    data = {**CONTENT, **{k: v for k, v in SECRET_VALUES.items()}}
    for key in RESPONSE_OMIT_KEYS:
        data.setdefault(key, f"value-of-{key}")

    cleaned = clean_meeting_data(data)

    for key in RESPONSE_OMIT_KEYS | _INTERNAL_DATA_KEYS:
        assert key not in cleaned, f"{key} is omitted from a non-owner response but delivered"
    assert not (SENSITIVE_OMIT_KEYS & set(cleaned))
    assert not (OWNER_ONLY_KEYS & set(cleaned))


def test_clean_meeting_data_drops_credential_shaped_keys_nobody_listed():
    """A key a future producer invents is withheld by default, not shipped until someone notices."""
    cleaned = clean_meeting_data({**CONTENT, "stripe_api_key": "sk_live_x", "refresh_token": "rt_x"})
    assert "stripe_api_key" not in cleaned and "refresh_token" not in cleaned


def test_clean_meeting_data_still_ships_the_meeting_content():
    """The deny-set must not become a content filter — subscribers render these."""
    cleaned = clean_meeting_data({**CONTENT, **SECRET_VALUES})
    assert cleaned == CONTENT, "meeting content must survive the filter unchanged"


def test_clean_meeting_data_leaves_names_that_merely_contain_a_sensitive_word():
    """``token_count``/``secret_santa_notes`` are content, not credentials (whole-key/tail match)."""
    data = {"token_count": 41, "secret_santa_notes": "n", "tokenizer": "whisper"}
    assert clean_meeting_data(data) == data


def test_clean_meeting_data_does_not_mutate_the_stored_row():
    """The row keeps every key — the delivery path itself reads the secret back out of it."""
    stored = {**CONTENT, **SECRET_VALUES}
    snapshot = json.loads(json.dumps(stored))
    clean_meeting_data(stored)
    assert stored == snapshot


# ── end to end: the bytes actually POSTed ────────────────────────────────────────────────────────


@dataclass
class _RecordingTransport:
    """Records the raw (url, body, headers) of every delivery."""

    received: List[Dict[str, Any]] = field(default_factory=list)

    async def __call__(self, url: str, body: bytes, headers: Dict[str, str]):
        self.received.append({"url": url, "body": body, "headers": dict(headers)})
        return _Resp(200)


@dataclass
class _Resp:
    status_code: int


@dataclass
class _CaptureSink:
    """Stands in for WebhookSink at the app seam, keeping the envelope it was handed."""

    calls: List[Dict[str, Any]] = field(default_factory=list)

    async def deliver(self, url, envelope, webhook_secret=None, *, scope="per-client",
                      events_config=None, label="", metadata=None):
        from meeting_api.webhooks import DeliveryResult

        self.calls.append({"url": url, "secret": webhook_secret, "envelope": envelope})
        return DeliveryResult(status="delivered", status_code=200)


def test_the_delivered_bytes_carry_no_credential_material():
    """A real sink, a real transport, the real envelope builder — assert on what went on the wire."""
    transport = _RecordingTransport()
    sink = WebhookSink(transport=transport, resolver=_PUBLIC)
    envelope = build_envelope(
        "meeting.completed",
        {"meeting": {"id": 1, "user_id": 7, "data": clean_meeting_data({**CONTENT, **SECRET_VALUES})}},
    )

    result = asyncio.run(sink.deliver(URL, envelope, SECRET, events_config={"meeting.completed": True}))

    assert result.status == "delivered"
    body = transport.received[0]["body"].decode()
    _assert_clean(body, where="delivered body")
    assert "Weekly sync" in body, "the content the subscriber subscribed to must still arrive"
    # The signing secret authenticates the delivery; it must never also be IN it.
    assert SECRET not in body


def test_the_lifecycle_callback_delivers_no_credential_material(goldens):
    """Whole path: seed a row carrying every class, fire the FSM, inspect the envelopes built for it.

    This is the shape the production leak had — nobody calls ``clean_meeting_data`` by hand; the
    lifecycle callback builds the meeting projection and hands it to the sink.

    The golden is the TERMINAL one deliberately. Only the typed envelope (``meeting.completed`` /
    ``bot.failed``) carries the durable row projection, and therefore ``meeting.data`` at all; a
    non-terminal ``meeting.status_change`` is built straight off the FSM record with a minimal
    meeting block (``app.py`` line ~530), so driving this with ``joining`` would pass against the
    unfixed source and prove nothing.
    """
    repo, sink = InMemoryMeetingRepo(), _CaptureSink()
    data = {
        **CONTENT,
        **SECRET_VALUES,
        "webhook_url": URL,
        "webhook_events": {"meeting.status_change": True},
    }
    data["webhook_secret"] = SECRET_VALUES["webhook_secret"]
    meeting = asyncio.run(repo.create_meeting(
        user_id=1, platform="google_meet", native_meeting_id="m1", data=data,
    ))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid="sess-uid"))

    client = TestClient(create_app(meeting_repo=repo, webhook_sink=sink))
    for case in ("joining", "active", "completed-stopped"):
        response = client.post("/bots/internal/callback/lifecycle", json=goldens[case])
        assert response.status_code == 200, response.text

    assert sink.calls, "no webhook delivered on FSM advance"
    types = [c["envelope"].get("event_type") for c in sink.calls]
    assert "meeting.completed" in types, f"the typed terminal envelope never fired: {types}"
    for call in sink.calls:
        _assert_clean(json.dumps(call["envelope"]), where=f"{call['envelope'].get('event_type')}")
    # Signing is unaffected: the sink is still handed the secret out of the stored row.
    assert sink.calls[0]["secret"] == SECRET_VALUES["webhook_secret"]
