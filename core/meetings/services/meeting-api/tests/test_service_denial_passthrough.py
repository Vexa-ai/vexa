"""A refusal must say why, in words the caller can act on.

`POST /bots` already answered a refused admission with `code`/`reason`/`decision_id` — enough for a
program to branch on, and nothing a person or an agent can DO anything with. The deciding service
is the only party that knows what would fix the account, so it is the only party that can author
that sentence; this fixture pins that meeting-api CARRIES it and never authors, edits or interprets
it.

Two properties matter more than the copying, and both are regressions waiting to happen:

  * **An unrecognised reason still reaches the caller.** There is no vocabulary in this service and
    there must not be one — a reason no build has heard of is exactly the case where the message is
    load-bearing.
  * **Optional means optional.** A deciding service that sends these fields must not break a
    deployment that has never heard of them, and one that sends nothing must produce the byte-same
    body as before. Absent fields are OMITTED, never null.

Nothing here names a plan, a price, a currency or a URL of ours. Every string is fixture-local.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.service_authority import (
    HttpServiceAuthority,
    ServiceAuthorityConfig,
    ServiceAuthorityDecision,
    ServiceAuthorityRequest,
)
from meeting_api.service_authority.models import (
    MESSAGE_MAX_CHARS,
    clean_action_url,
    clean_message,
)


UTC = timezone.utc


class DenyingAuthority:
    """Refuses every admission with a decision the fixture supplies verbatim."""

    configured = True
    mode = "enforce"

    def __init__(self, decision: ServiceAuthorityDecision) -> None:
        self.decision = decision

    async def decide(
        self,
        request: ServiceAuthorityRequest,
    ) -> ServiceAuthorityDecision:
        return replace(
            self.decision,
            request_id=request.request_id,
            service_identity=request.service_identity,
        )


def _denial(
    *,
    reason: str = "fixture_gate_closed",
    decision_id: str = "decision-fixture-1",
    message: str | None = None,
    action_url: str | None = None,
) -> ServiceAuthorityDecision:
    return ServiceAuthorityDecision(
        authority_version="service-authority.v1",
        decision_id=decision_id,
        request_id="fixture-request",
        service_identity="fixture-service",
        allow=False,
        reason=reason,
        decided_at=datetime.now(UTC),
        message=message,
        action_url=action_url,
    )


def _refuse(decision: ServiceAuthorityDecision) -> dict:
    """Drive `POST /bots` into the refusal and hand back the `detail` object."""
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    client = TestClient(create_app(
        meeting_repo=repo,
        runtime=runtime,
        service_authority=DenyingAuthority(decision),
        token_secret="fixture-token-secret",
    ))
    response = client.post(
        "/bots",
        headers={"x-user-id": "41"},
        json={
            "platform": "google_meet",
            "native_meeting_id": "denial-passthrough",
            "transcribe_enabled": False,
            "recording_enabled": False,
        },
    )
    assert response.status_code == 403
    # Nothing was spawned: the words are an addition to the refusal, never a softening of it.
    assert repo._meetings == {}
    assert runtime.specs == []
    return response.json()["detail"]


# --- the copy itself -------------------------------------------------------------------------

def test_present_message_and_action_url_are_copied_verbatim() -> None:
    detail = _refuse(_denial(
        message="This account cannot start bots right now. Open the account page to fix it.",
        action_url="https://example.invalid/account",
    ))
    assert detail == {
        "code": "service_not_allowed",
        "reason": "fixture_gate_closed",
        "decision_id": "decision-fixture-1",
        "message": "This account cannot start bots right now. Open the account page to fix it.",
        "action_url": "https://example.invalid/account",
    }


def test_absent_fields_are_omitted_not_nulled() -> None:
    """The pre-change body, byte for byte.

    A deployment whose authority says nothing extra must be indistinguishable from one built
    before this seam existed — and a consumer reading `"message" in detail` must not be told yes
    by a null.
    """
    detail = _refuse(_denial())
    assert detail == {
        "code": "service_not_allowed",
        "reason": "fixture_gate_closed",
        "decision_id": "decision-fixture-1",
    }
    assert "message" not in detail
    assert "action_url" not in detail


def test_one_field_present_carries_alone() -> None:
    """Words with no link is a normal shape — a refusal nobody can self-serve out of."""
    detail = _refuse(_denial(message="Nothing you can do from here; ask your administrator."))
    assert detail["message"] == "Nothing you can do from here; ask your administrator."
    assert "action_url" not in detail


def test_unknown_reason_passes_through_unchanged() -> None:
    """No allow-list. A reason this build has never seen still reaches the caller intact."""
    detail = _refuse(_denial(
        reason="a_reason_no_build_has_ever_heard_of",
        message="Fixture words for an unknown gate.",
    ))
    assert detail["reason"] == "a_reason_no_build_has_ever_heard_of"
    assert detail["message"] == "Fixture words for an unknown gate."


# --- sanitising: a bad courtesy field is dropped, never fatal ---------------------------------

def test_oversize_message_is_truncated_not_rejected() -> None:
    detail = _refuse(_denial(message=clean_message("x" * (MESSAGE_MAX_CHARS + 500))))
    assert len(detail["message"]) == MESSAGE_MAX_CHARS
    # The refusal itself is untouched by the trim.
    assert detail["code"] == "service_not_allowed"


@pytest.mark.parametrize(
    "hostile",
    [
        "http://example.invalid/account",       # plaintext
        "javascript:alert(1)",                  # executes in the reader
        "data:text/html,<script>x</script>",    # carries its own payload
        "//example.invalid/account",            # scheme-relative
        "/account",                             # path only
        "https://",                             # no host
        "ftp://example.invalid/account",
    ],
)
def test_non_https_action_url_is_dropped(hostile: str) -> None:
    assert clean_action_url(hostile) is None
    detail = _refuse(_denial(message="Words survive.", action_url=clean_action_url(hostile)))
    assert "action_url" not in detail
    assert detail["message"] == "Words survive."


def test_control_characters_cannot_forge_a_second_line() -> None:
    assert clean_message("first line\nSTATUS: allowed") == "first lineSTATUS: allowed"
    assert clean_message("   \n\t  ") is None
    assert clean_message(None) is None
    assert clean_message(42) is None


# --- the wire: the field allow-list must not reject a decision that carries them ---------------

async def _wire_denial(extra: dict) -> ServiceAuthorityDecision:
    """Parse a decision straight off a stubbed authority response carrying `extra`."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "authority_version": "service-authority.v1",
            "decision_id": "decision-wire-1",
            "request_id": body["request_id"],
            "service_identity": body["service_identity"],
            "allow": False,
            "reason": "fixture_gate_closed",
            "decided_at": datetime.now(UTC).isoformat(),
            **extra,
        })

    authority = HttpServiceAuthority(
        ServiceAuthorityConfig(
            url="https://authority.invalid/decide",
            secret="fixture-authority-secret",
            mode="enforce",
            timeout_seconds=5,
            response_max_age_seconds=60,
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    request = ServiceAuthorityRequest.admit(
        user_id=41,
        request_id="meeting-session:fixture:admit",
        service_identity="meeting-session:fixture",
        transcription_provider="none",
        active_concurrency=0,
    )
    return await authority.decide(request)


@pytest.mark.asyncio
async def test_wire_decision_carrying_words_is_still_a_decision() -> None:
    """The regression this change exists to prevent.

    The strict unknown-field check rejected the WHOLE response when the deciding service added
    these fields, and the raised ValueError became `ServiceAuthorityUnavailable` — so a refusal
    that had gained plain words for the caller came back as a 503 outage instead of a 403 they
    could act on. Adding a field on the deciding side must never do that.
    """
    decision = await _wire_denial({
        "message": "Fixture words the caller can act on.",
        "action_url": "https://example.invalid/account",
    })
    assert decision.allow is False
    assert decision.reason == "fixture_gate_closed"
    assert decision.message == "Fixture words the caller can act on."
    assert decision.action_url == "https://example.invalid/account"


@pytest.mark.asyncio
async def test_wire_decision_without_words_is_unchanged() -> None:
    decision = await _wire_denial({})
    assert decision.message is None
    assert decision.action_url is None
    assert decision.caller_fields() == {}


@pytest.mark.asyncio
async def test_wire_hostile_fields_are_sanitised_without_losing_the_decision() -> None:
    decision = await _wire_denial({
        "message": "y" * (MESSAGE_MAX_CHARS + 100),
        "action_url": "javascript:alert(1)",
    })
    assert decision.allow is False
    assert len(decision.message) == MESSAGE_MAX_CHARS
    assert decision.action_url is None


# --- A10: the trap is the CLASS, not the two names ---------------------------------------------

@pytest.mark.asyncio
async def test_an_unknown_optional_field_does_not_cost_the_customer_the_decision() -> None:
    """THE CLASS the two-name widening left open.

    `message` / `action_url` were added to the allow-list because, without them, a decider that
    started sending them turned an actionable 403 into a 503 outage. That fixed one instance. The
    NEXT optional field any decider adds reproduced it exactly — and a decider and a meeting-api
    are separately deployed, which is the whole reason this contract exists.

    So the runtime door now IGNORES what it does not recognise and still validates everything it
    does. The decision survives; only the field we cannot read is lost."""
    decision = await _wire_denial({
        "message": "Fixture words the caller can act on.",
        "some_field_a_newer_decider_sends": {"nested": ["anything", 1, None]},
    })
    assert decision.allow is False
    assert decision.reason == "fixture_gate_closed"
    assert decision.message == "Fixture words the caller can act on."
    assert not hasattr(decision, "some_field_a_newer_decider_sends")


@pytest.mark.asyncio
async def test_tolerating_unknown_fields_does_not_stop_validating_the_known_ones() -> None:
    """Tolerant is not lax. An unknown key rides along; a MALFORMED KNOWN key still refuses, and
    the adapter still turns that into `ServiceAuthorityUnavailable` rather than a bad decision."""
    from meeting_api.service_authority import ServiceAuthorityUnavailable

    with pytest.raises(ServiceAuthorityUnavailable):
        # `allow` is the field the whole decision turns on; a string is not a decision.
        await _wire_denial({"allow": "no", "whatever_else": "x"})

    with pytest.raises(ServiceAuthorityUnavailable):
        # An admission decision may not carry a stop scope — a cross-field rule, still enforced.
        await _wire_denial({"stop_scope": "billable_service", "whatever_else": "x"})


def test_strict_mode_still_refuses_and_is_for_the_contract_test_only() -> None:
    """The strictness has not been deleted — it has MOVED to where an unexpected key means our own
    fixture drifted from the contract, rather than "the other side shipped first". No production
    path passes `strict=True`; `adapters.HttpServiceAuthority` parses tolerantly."""
    import inspect

    from meeting_api.service_authority import adapters as authority_adapters

    now = datetime.now(UTC)
    request = ServiceAuthorityRequest.admit(
        user_id=41,
        request_id="meeting-session:fixture:admit",
        service_identity="meeting-session:fixture",
        transcription_provider="none",
        active_concurrency=0,
    )
    wire = {
        "authority_version": "service-authority.v1",
        "decision_id": "decision-wire-1",
        "request_id": request.request_id,
        "service_identity": request.service_identity,
        "allow": False,
        "reason": "fixture_gate_closed",
        "decided_at": now.isoformat(),
        "a_field_the_contract_does_not_declare": "x",
    }
    with pytest.raises(ValueError, match="unknown fields"):
        ServiceAuthorityDecision.from_wire(
            wire, request=request, now=now, max_age_seconds=60, strict=True)
    # …and the same body is a decision without the flag.
    assert ServiceAuthorityDecision.from_wire(
        wire, request=request, now=now, max_age_seconds=60).allow is False
    assert "strict" not in inspect.getsource(authority_adapters.HttpServiceAuthority.decide)
