"""Conformance of the control runtime against the sealed zaki-control.v1 goldens.

Every vector under `contracts/zaki-control.v1/golden/` is replayed through THIS service's own
primitives — the router's canonicalization, binding and URL predicate, and the callback
dispatcher's lifecycle graph and signing — rather than through a second copy of the rules. A
golden that stops discriminating is therefore a statement about the runtime, not about the test.

The convention mirrors the sealed `validate.mjs`: the `$def` is the filename prefix, and a
`.invalid-` infix marks a vector the implementation must REJECT.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path

import jsonschema
import pytest

from meeting_api.zaki_control.callbacks import _ADJACENCY, _legal_path
from meeting_api.zaki_control.router import (
    ControlConfig,
    _binding,
    _canonical_sha256,
    _mutation_headers_match,
    meeting_url_matches_platform,
)
from meeting_api.zaki_control.schema import REGISTRY, SCHEMA


CONTRACT = Path(__file__).resolve().parents[3] / "contracts/zaki-control.v1"
GOLDEN = CONTRACT / "golden"
# The sealed harness signs its vectors with this fixed key so conformance never needs a real
# service secret in a fixture (contract README, "Callback authentication").
CALLBACK_TEST_KEY = b"zaki-control-v1-contract-test-key"

MUTATION_SHAPES = (
    ("ensure", "EnsureRequest"),
    ("capture", "CaptureRequest"),
    ("stop_capture", "StopCaptureRequest"),
    ("erase_meeting", "EraseMeetingRequest"),
    ("erase_account", "EraseAccountRequest"),
)


def _validator(shape: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{shape}"},
        registry=REGISTRY,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _schema_ok(value: object, shape: str) -> bool:
    return _validator(shape).is_valid(value)


def _mutation_operation(request: object) -> tuple[str, str] | None:
    """Infer the operation from the closed request shape, as the sealed harness does."""
    matches = [
        (operation, shape)
        for operation, shape in MUTATION_SHAPES
        if _schema_ok(request, shape)
    ]
    return matches[0] if len(matches) == 1 else None


def _capture_request_errors(request: object, jitsi_hosts: tuple[str, ...] = ()) -> list[str]:
    errors: list[str] = []
    if not isinstance(request, dict):
        return ["capture request must be an object"]
    attestation = request.get("capture_attestation")
    subject = request.get("subject")
    attested_by = attestation.get("attested_by_user_id") if isinstance(attestation, dict) else None
    subject_user = subject.get("user_id") if isinstance(subject, dict) else None
    if attested_by != subject_user:
        errors.append("capture attestation user must match the bound subject")
    if not meeting_url_matches_platform(
        request.get("platform"), request.get("meeting_url"), jitsi_hosts
    ):
        errors.append("meeting URL must match the declared platform and validation context")
    return errors


BINDING_SECRET = "zaki-control-conformance-signing-secret-0123456789"
BINDING_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _mint_token(scope: dict) -> str:
    """Mint a token the production verifier accepts, carrying the vector's declared token scope."""
    claims = {
        "aud": "zaki-control.v1",
        "exp": int(BINDING_NOW.timestamp()) + 60,
        "iat": int(BINDING_NOW.timestamp()),
        "tenant_id": scope["tenant_id"],
        "user_id": scope["user_id"],
        "v": 1,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(
        hmac.new(BINDING_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{payload}.{signature}"


def _binding_errors(data: dict) -> list[str]:
    """Drive the four-way identity rule through the PRODUCTION binding functions.

    The vector supplies `token_scope` as data, so the scope is minted into a real token and handed
    to the real `_binding` — otherwise this would be a second copy of the rule, and a regression in
    `_binding` would leave the goldens still green.
    """
    errors: list[str] = []
    request = data["request"]
    headers = data["headers"]
    subject, code = _binding(
        token=_mint_token(data["token_scope"]),
        config=ControlConfig(
            enabled=True, operator_enabled=True, signing_secret=BINDING_SECRET
        ),
        path_user_id=data["path_user_id"],
        tenant_header=headers["X-Zaki-Tenant-Id"],
        user_header=headers["X-Zaki-User-Id"],
        body=request,
        now=BINDING_NOW,
    )
    if code is not None or subject is None:
        errors.append(f"binding rejected the request as {code}")
    if not _mutation_headers_match(
        request, headers["X-Request-Id"], headers["Idempotency-Key"]
    ):
        errors.append("request/idempotency headers must equal their body fields")
    return errors


def _lifecycle_errors(data: dict) -> list[str]:
    """Walk each sequence against the runtime's OWN adjacency graph."""
    errors: list[str] = []
    for sequence in data["sequences"]:
        for previous, current in zip(sequence, sequence[1:]):
            if current not in _ADJACENCY.get(previous, ()):
                errors.append(f"illegal lifecycle transition {previous} -> {current}")
    return errors


def _idempotency_errors(data: dict) -> list[str]:
    """Replay attempts through the router's real canonicalization and namespace rule."""
    errors: list[str] = []
    records: dict[tuple, dict] = {}
    outcomes: list[str] = []
    for attempt in data["attempts"]:
        request = attempt["request"]
        if attempt["response_request_id"] != request["request_id"]:
            errors.append("idempotency response must echo the current attempt request ID")
        mutation = _mutation_operation(request)
        if mutation is None:
            errors.append("idempotency attempt must contain exactly one recognized mutation request")
            outcomes.append("invalid")
            continue
        operation, shape = mutation
        semantic = _capture_request_errors(request) if shape == "CaptureRequest" else []
        if semantic:
            errors.extend(f"idempotency attempt: {error}" for error in semantic)
            outcomes.append("invalid")
            continue
        namespace = (
            request["api_version"],
            request["subject"]["tenant_id"],
            request["subject"]["user_id"],
            operation,
            request["idempotency_key"],
        )
        request_hash = _canonical_sha256(request)
        previous = records.get(namespace)
        if previous is None:
            if attempt.get("result_sha256") is None:
                errors.append("an applied idempotency attempt must carry a successful result fingerprint")
            records[namespace] = {
                "request_hash": request_hash,
                "result_sha256": attempt.get("result_sha256"),
            }
            outcomes.append("applied")
            continue
        if previous["request_hash"] != request_hash:
            if attempt.get("result_sha256") is not None:
                errors.append("an idempotency conflict must not carry a successful result fingerprint")
            outcomes.append("conflict")
            continue
        if attempt.get("result_sha256") is None:
            errors.append("a replayed idempotency attempt must carry the original result fingerprint")
        elif attempt["result_sha256"] != previous["result_sha256"]:
            errors.append("an idempotency replay must return the original operation result")
        outcomes.append("replayed")
    if outcomes != data["expected_outcomes"]:
        errors.append("idempotency outcomes do not match owner/operation-scoped replay semantics")
    return errors


def _callback_signature_errors(data: dict) -> list[str]:
    """Verify the vector against the exact bytes this service signs when it SENDS a callback."""
    errors: list[str] = []
    headers = data["headers"]
    timestamp = headers["X-Webhook-Timestamp"]
    try:
        signed_at = int(timestamp)
    except (TypeError, ValueError):
        return ["callback timestamp is not an integer"]
    if abs(data["received_at_unix"] - signed_at) > 300:
        errors.append("callback signature is outside the 300-second replay window")
    expected = "sha256=" + hmac.new(
        CALLBACK_TEST_KEY,
        f"{timestamp}.".encode() + data["raw_body"].encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(headers["X-Webhook-Signature"], expected):
        errors.append("callback signature does not authenticate timestamp.raw_body")
    try:
        body = json.loads(data["raw_body"])
    except json.JSONDecodeError:
        return errors + ["raw callback body is not JSON"]
    if not _schema_ok(body, "CallbackEnvelope"):
        errors.append("raw callback body does not conform to CallbackEnvelope")
    return errors


def _settlement_errors(data: dict) -> list[str]:
    """The cumulative settlement algorithm a consumer of these callbacks must implement.

    The engine is the SENDER, so this is the receiving contract its event stream has to satisfy:
    ordering, duplicate suppression, monotonic totals and exactly-once terminal finalization.
    """
    errors: list[str] = []
    applied: list[str] = []
    ignored: list[str] = []
    seen: dict[str, str] = {}
    applied_sequences: dict[int, tuple[int, bool]] = {}
    identity: str | None = None
    final_sequence = 0
    captured_total = 0
    terminal = False

    for event in data["events"]:
        if event["event_type"] != "minutes.capture.usage":
            errors.append("usage settlement vectors may contain only minutes.capture.usage events")
            continue
        payload = event["data"]
        current_identity = json.dumps(
            {
                "subject": payload["subject"],
                "operation_id": payload["operation_id"],
                "capture_id": payload["capture_id"],
                "meeting_id": payload["meeting_id"],
                "reservation_id": payload["metering"]["reservation_id"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity is None:
            identity = current_identity
        elif identity != current_identity:
            errors.append("usage settlement events must share one subject and metering identity")

        fingerprint = json.dumps(event, sort_keys=True, separators=(",", ":"))
        if event["event_id"] in seen:
            if seen[event["event_id"]] != fingerprint:
                errors.append(f"usage event {event['event_id']} conflicts with an already recorded payload")
            ignored.append(event["event_id"])
            continue
        seen[event["event_id"]] = fingerprint

        metering = payload["metering"]
        sequence = metering["sequence"]
        total = metering["captured_seconds_total"]
        event_terminal = metering["terminal"]

        # A reused sequence is checked for conflicting values BEFORE the post-terminal no-effect
        # rule, so a contradiction is never masked by an earlier finalization.
        prior = applied_sequences.get(sequence)
        if prior is not None:
            if prior != (total, event_terminal):
                errors.append(f"usage sequence {sequence} conflicts with an already applied cumulative value")
            ignored.append(event["event_id"])
            continue
        if terminal:
            ignored.append(event["event_id"])
            continue
        if sequence < final_sequence:
            if total > captured_total:
                errors.append(f"stale usage sequence {sequence} exceeds the applied cumulative total")
            ignored.append(event["event_id"])
            continue
        if total < captured_total:
            errors.append(f"usage total decreases at sequence {sequence}")
            ignored.append(event["event_id"])
            continue

        applied.append(event["event_id"])
        applied_sequences[sequence] = (total, event_terminal)
        final_sequence = sequence
        captured_total = total
        terminal = event_terminal

    expected = data["expected"]
    if applied != expected["applied_event_ids"]:
        errors.append("applied usage event identities do not match expected settlement")
    if ignored != expected["ignored_event_ids"]:
        errors.append("ignored usage event identities do not match expected settlement")
    if final_sequence != expected["final_sequence"]:
        errors.append("final usage sequence does not match expected settlement")
    if captured_total != expected["final_captured_seconds_total"]:
        errors.append("final cumulative usage does not match expected settlement")
    if terminal != expected["terminal"]:
        errors.append("terminal settlement state does not match expected settlement")
    return errors


def _semantic_errors(shape: str, data: object) -> list[str]:
    if shape == "EnsureRequest":
        retention = data.get("policy", {}).get("retention", {}) if isinstance(data, dict) else {}
        if retention.get("summary_days", 0) > retention.get("transcript_days", 0):
            return ["summary retention cannot outlive transcript retention"]
        return []
    if shape == "CaptureRequest":
        return _capture_request_errors(data)
    if shape == "CaptureRequestValidationVector":
        return _capture_request_errors(
            data["request"], tuple(data["configured_jitsi_hosts"])
        )
    if shape == "ControlRequestBindingVector":
        return _binding_errors(data)
    if shape == "LifecycleTransitionVector":
        return _lifecycle_errors(data)
    if shape == "CallbackVerificationVector":
        return _callback_signature_errors(data)
    if shape == "UsageSettlementVector":
        return _settlement_errors(data)
    if shape == "IdempotencyReplayVector":
        return _idempotency_errors(data)
    return []


GOLDEN_FILES = sorted(path.name for path in GOLDEN.glob("*.json"))


def test_golden_corpus_is_present():
    """A silently empty corpus would make every conformance case vacuously pass."""
    assert len(GOLDEN_FILES) >= 65, f"expected the sealed corpus, found {len(GOLDEN_FILES)}"


@pytest.mark.parametrize("filename", GOLDEN_FILES)
def test_golden_discriminates(filename: str):
    shape = filename.split(".")[0]
    data = json.loads((GOLDEN / filename).read_text())
    schema_valid = _schema_ok(data, shape)
    semantics = _semantic_errors(shape, data) if schema_valid else []
    valid = schema_valid and not semantics
    # `.invalid-` vectors must be REJECTED; everything else must be accepted.
    expected = ".invalid-" not in filename
    detail = "; ".join(semantics) or "schema validation failed"
    assert valid is expected, (
        f"{filename} should {'conform' if expected else 'be rejected'}: {detail}"
        if expected
        else f"{filename} was accepted but must be rejected"
    )


def test_lifecycle_graph_matches_the_sealed_adjacency():
    """The runtime's graph IS the contract graph — not a superset that quietly permits more."""
    assert _ADJACENCY == {
        "requested": ("joining", "failed"),
        "joining": ("awaiting_admission", "active", "failed"),
        "awaiting_admission": ("active", "failed"),
        "active": ("stopping", "completed", "failed"),
        "stopping": ("completed", "failed"),
        "completed": (),
        "failed": (),
    }


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_terminal_states_have_no_successor(terminal: str):
    for target in _ADJACENCY:
        if target != terminal:
            assert _legal_path(terminal, target) is None


def test_a_skipped_join_is_not_reachable_in_one_step():
    """`requested` must pass through `joining`; it can never jump straight to `active`."""
    assert "active" not in _ADJACENCY["requested"]
    assert _legal_path("requested", "active") == ("joining", "active")


def test_recovery_walk_never_moves_backwards():
    """The erasure path drives an in-flight capture to a terminal state; it must not rewind."""
    assert _legal_path("stopping", "completed") == ("completed",)
    assert _legal_path("stopping", "joining") is None


# ── host-authority cases the sealed corpus does not isolate ──────────────────────────────────────
# `CaptureRequest.invalid-platform-url-mismatch` pairs google_meet with `https://zoom.us/j/<digits>`,
# whose path ALSO fails the meeting-code rule — so no golden makes the host check the deciding gate.
# Deleting the host comparison entirely still leaves every golden discriminating. These pin it.

@pytest.mark.parametrize(
    "platform,url",
    [
        # A lookalike host carrying an otherwise perfectly valid meeting code.
        ("google_meet", "https://meet.evil.example/abc-defg-hij"),
        ("google_meet", "https://meet.google.com.evil.example/abc-defg-hij"),
        ("google_meet", "https://notmeet.google.com/abc-defg-hij"),
        # Credentials in the URL are refused however well-formed the rest is.
        ("google_meet", "https://user:pw@meet.google.com/abc-defg-hij"),
        # The lookup path is explicitly excluded even on the real host.
        ("google_meet", "https://meet.google.com/lookup/abc-defg-hij"),
        # Plaintext is never acceptable.
        ("google_meet", "http://meet.google.com/abc-defg-hij"),
        # Name fragments confer no trust on the Jitsi plane.
        ("jitsi", "https://meet.jit.si.evil.example/Room42"),
        ("jitsi", "https://jitsi-lookalike.example/Room42"),
        # A zoom-shaped path on a non-zoom host.
        ("zoom", "https://zoom.us.evil.example/j/98765432101"),
        # A teams-shaped path on a non-teams host.
        ("teams", "https://teams.microsoft.com.evil.example/meet/123456789012"),
    ],
)
def test_host_authority_is_required_beyond_path_shape(platform: str, url: str):
    assert meeting_url_matches_platform(platform, url) is False


@pytest.mark.parametrize(
    "platform,url",
    [
        ("google_meet", "https://meet.google.com/abc-defg-hij"),
        ("zoom", "https://acme.zoom.us/j/98765432101?pwd=fixture"),
        ("teams", "https://teams.microsoft.com/meet/123456789012?p=fixture"),
        ("jitsi", "https://meet.jit.si/ZakiRoom42"),
    ],
)
def test_legitimate_provider_urls_still_pass(platform: str, url: str):
    """The negative cases above must not have been bought with a predicate that rejects everything."""
    assert meeting_url_matches_platform(platform, url) is True


def test_operator_jitsi_host_comes_only_from_validated_configuration():
    """An operator host is honoured when passed in, and never sourced from ambient environment."""
    assert meeting_url_matches_platform("jitsi", "https://video.corp/ZakiRoom") is False
    assert meeting_url_matches_platform(
        "jitsi", "https://video.corp/ZakiRoom", ("video.corp",)
    ) is True


# ── lifecycle-guard regressions against the REAL dispatcher ──────────────────────────────────────
# The seam tests drive erasure/stop through a simplified `_SettlingDispatcher` double that records
# transitions unconditionally, so it cannot observe the adjacency guard at all. These exercise the
# production `ControlCallbackDispatcher`, where the guard and the state walk actually live.

from dataclasses import replace  # noqa: E402

from meeting_api.zaki_control.callbacks import ControlCallbackDispatcher, capture_seconds_at  # noqa: E402
from meeting_api.zaki_control.fakes import InMemoryControlStore  # noqa: E402
from meeting_api.zaki_control.ports import Capture, Subject  # noqa: E402

_CAPTURE = Capture(
    capture_id="cap-1", subject=Subject(tenant_id="tenant-1", user_id="42"),
    operation_id="op-1", reservation_id="reserve-1", platform="google_meet",
    native_meeting_id="abc-defg-hij", meeting_id="meeting-1", state="active",
)


def _dispatcher(state: str = "active"):
    store = InMemoryControlStore()
    capture = replace(_CAPTURE, state=state)
    store.captures[capture.capture_id] = capture
    return store, capture, ControlCallbackDispatcher(
        store,
        callback_url="https://hub.example/api/minutes/callback/v1",
        hmac_key="hub-callback-hmac-key-0123456789abcdef",
    )


async def test_withdrawal_walk_queues_terminal_settlement():
    """Erasure of an in-flight capture must reach a terminal state AND queue its settlement.

    Regression: the walk starts from `capture.state`, so handing the dispatcher a copy already
    advanced to the target made every walk a no-op — nothing was queued, the terminal-settlement
    check never passed, and erasure failed closed forever instead of converging.
    """
    store, capture, dispatcher = _dispatcher("active")

    await dispatcher.record_capture_timeline(capture, state="stopping")
    stopping = store.captures["cap-1"]
    await dispatcher.record_capture_timeline(stopping, state="completed")

    assert store.captures["cap-1"].state == "completed"
    assert await store.terminal_callbacks_delivered("cap-1") is False  # queued, not yet delivered
    queued = await store.pending_callbacks(limit=50, capture_id="cap-1")
    states = [event.body["data"].get("state") for event in queued]
    assert "stopping" in states and "completed" in states
    # Draining the outbox is what unblocks erasure.
    for event in queued:
        await store.mark_callback_delivered(event.event_id)
    assert await store.terminal_callbacks_delivered("cap-1") is True


async def test_a_pre_advanced_capture_records_nothing():
    """Pins the exact defect: target == current is a no-op, so the caller must not pre-advance."""
    store, capture, dispatcher = _dispatcher("active")

    await dispatcher.record_capture_timeline(replace(capture, state="stopping"), state="stopping")

    assert await store.pending_callbacks(limit=50, capture_id="cap-1") == ()
    assert store.captures["cap-1"].state == "active"


async def test_status_guard_refuses_a_skipped_join_on_the_real_dispatcher():
    """`requested -> stopping` is not in the sealed graph and must not be written."""
    store, capture, dispatcher = _dispatcher("requested")

    await dispatcher.record_capture_status(capture, state="stopping")

    assert store.captures["cap-1"].state == "requested"
    assert await store.pending_callbacks(limit=50, capture_id="cap-1") == ()


async def test_status_guard_allows_an_idempotent_reassert():
    """Re-asserting the current state is how a crashed terminal settlement converges."""
    store, capture, dispatcher = _dispatcher("completed")

    await dispatcher.record_capture_status(capture, state="completed")

    assert store.captures["cap-1"].state == "completed"
    assert len(await store.pending_callbacks(limit=50, capture_id="cap-1")) > 0


async def test_terminal_settlement_carries_true_captured_seconds():
    """WP-M8/H15 regression: `captured_seconds_total` was only ever computed on
    the ERASURE path, so every normal terminal usage event carried the default 0
    and the Hub settled a full refund — real bot compute given away, and a
    lifetime-reaped one-hour capture would refund the whole hour. The REAL
    dispatcher must stamp wall-clock seconds (bounded by the cap) at the
    terminal transition."""
    from datetime import datetime, timedelta, timezone

    fixed_now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    store = InMemoryControlStore()
    capture = replace(
        _CAPTURE,
        state="active",
        started_at=fixed_now - timedelta(seconds=90),
        max_capture_seconds=3600,
    )
    store.captures[capture.capture_id] = capture
    dispatcher = ControlCallbackDispatcher(
        store,
        callback_url="https://hub.example/api/minutes/callback/v1",
        hmac_key="hub-callback-hmac-key-0123456789abcdef",
        now=lambda: fixed_now,
    )

    await dispatcher.record_capture_timeline(capture, state="stopping")
    await dispatcher.record_capture_timeline(store.captures["cap-1"], state="completed")

    usage = [
        event for event in await store.pending_callbacks(limit=50, capture_id="cap-1")
        if event.body.get("event_type") == "minutes.capture.usage"
    ]
    assert usage, "terminal settlement event missing"
    totals = [event.body["data"]["metering"]["captured_seconds_total"] for event in usage]
    assert totals[-1] == 90, totals

    capped = replace(
        _CAPTURE,
        capture_id="cap-2",
        state="active",
        started_at=fixed_now - timedelta(seconds=99999),
        max_capture_seconds=3600,
    )
    store.captures[capped.capture_id] = capped
    await dispatcher.record_capture_timeline(capped, state="stopping")
    await dispatcher.record_capture_timeline(store.captures["cap-2"], state="failed")
    capped_usage = [
        event for event in await store.pending_callbacks(limit=50, capture_id="cap-2")
        if event.body.get("event_type") == "minutes.capture.usage"
    ]
    assert capped_usage and capped_usage[-1].body["data"]["metering"]["captured_seconds_total"] == 3600


async def test_reconcile_replays_the_minimal_legal_path_for_an_advanced_meeting():
    """The reconcile loop crash-looped in staging on the first real capture:
    WP-M6 shipped `reconcile_capture_lifecycle` consuming `_RECOVERY_STEPS`
    without ever defining it, and no test drove the path (NameError every
    tick). This drives the REAL dispatcher through reconciliation for a
    meeting that reached `completed` while the control mapping was down, and
    asserts the capture is walked requested→joining→active→completed with a
    terminal settlement queued."""
    store = InMemoryControlStore()
    capture = replace(_CAPTURE, state="requested")
    store.captures[capture.capture_id] = capture
    dispatcher = ControlCallbackDispatcher(
        store,
        callback_url="https://hub.example/api/minutes/callback/v1",
        hmac_key="hub-callback-hmac-key-0123456789abcdef",
    )

    await dispatcher.reconcile_capture_lifecycle(
        {"id": capture.meeting_id, "status": "completed", "data": {}}
    )

    assert store.captures["cap-1"].state == "completed"
    states = [
        event.body["data"].get("state")
        for event in await store.pending_callbacks(limit=50, capture_id="cap-1")
        if event.body.get("event_type") == "minutes.capture.status"
    ]
    assert states == ["joining", "active", "completed"], states
    usage = [
        event for event in await store.pending_callbacks(limit=50, capture_id="cap-1")
        if event.body.get("event_type") == "minutes.capture.usage"
    ]
    assert usage, "terminal settlement must be queued by the replay"


def test_capture_seconds_survive_naive_db_timestamps():
    """The DB driver hands back NAIVE datetimes (TIMESTAMP WITHOUT TIME ZONE,
    stored UTC) while the clock injects aware-UTC. The subtraction raised
    TypeError on the FIRST stop of a real joined meeting and crash-looped the
    callback drain: captures wedged at `stopping`, the outbox backed up, and
    settlements read 0 seconds. Naive means UTC by storage contract."""
    aware_now = datetime(2026, 7, 21, 12, 3, 0, tzinfo=timezone.utc)
    naive_start = datetime(2026, 7, 21, 12, 1, 30)  # exactly 90s earlier, no tzinfo
    capture = replace(_CAPTURE, started_at=naive_start, captured_seconds_total=0,
                      max_capture_seconds=3600)
    assert capture_seconds_at(capture, aware_now) == 90
    # and the mirror shape: naive clock against an aware row
    aware_start = datetime(2026, 7, 21, 12, 1, 30, tzinfo=timezone.utc)
    naive_now = datetime(2026, 7, 21, 12, 3, 0)
    capture = replace(_CAPTURE, started_at=aware_start, captured_seconds_total=0,
                      max_capture_seconds=3600)
    assert capture_seconds_at(capture, naive_now) == 90
    # `ended_at` crosses the same boundary: the reconcile projection hands back a
    # NAIVE meetings.end_time while the started anchor may already be aware.
    assert capture_seconds_at(capture, aware_now, ended_at=datetime(2026, 7, 21, 12, 3, 0)) == 90
    naive_capture = replace(_CAPTURE, started_at=datetime(2026, 7, 21, 12, 1, 30),
                            captured_seconds_total=0, max_capture_seconds=3600)
    assert capture_seconds_at(naive_capture, naive_now, ended_at=aware_now) == 90


# ── WP-M9/A meter-truth regressions ──────────────────────────────────────────────────────────────
# Live settlements read 207s/782s/2033s for meetings that captured ~0-2 minutes: the row mapping
# anchored the meter on the row's created_at when the bot never reached active (lobby billing),
# and the reconcile path settled at wall-clock-at-reconcile instead of the meeting's end.

from datetime import timedelta  # noqa: E402


def _meter_dispatcher(store, at):
    return ControlCallbackDispatcher(
        store,
        callback_url="https://hub.example/api/minutes/callback/v1",
        hmac_key="hub-callback-hmac-key-0123456789abcdef",
        now=lambda: at,
    )


async def _usage_totals(store, capture_id):
    return [
        event.body["data"]["metering"]["captured_seconds_total"]
        for event in await store.pending_callbacks(limit=50, capture_id=capture_id)
        if event.body.get("event_type") == "minutes.capture.usage"
    ]


def test_row_mapping_never_anchors_the_meter_on_creation_time():
    """A capture whose bot never reached active (meetings.start_time NULL) has no
    meterable window. Mapping created_at into started_at anchored the meter on
    CREATION time: a lobby-only capture settled 782s of billable seconds for a
    bot that recorded nothing."""
    from meeting_api.zaki_control.adapters import SqlAlchemyControlStore

    row = {
        "capture_id": "cap-row", "tenant_id": "tenant-1", "user_id": "42",
        "operation_id": "op-1", "reservation_id": "reserve-1", "platform": "google_meet",
        "native_meeting_id": "abc-defg-hij", "meeting_id": 7, "state": "failed",
        "failure_code": "join_denied", "captured_seconds_total": 0,
        "max_capture_seconds": 3600,
        "start_time": None, "end_time": None,
        "created_at": datetime(2026, 7, 21, 11, 47, 13),
    }
    capture = SqlAlchemyControlStore._capture_from_row(row)
    assert capture.started_at is None
    # ...so a settlement computed however late releases the hold in full.
    late = datetime(2026, 7, 21, 13, 0, 0, tzinfo=timezone.utc)
    assert capture_seconds_at(capture, late) == 0
    # An actually-active row still anchors on start_time, never created_at.
    active = SqlAlchemyControlStore._capture_from_row(
        {**row, "state": "active", "failure_code": None,
         "start_time": datetime(2026, 7, 21, 12, 0, 0)}
    )
    assert active.started_at == datetime(2026, 7, 21, 12, 0, 0)


def _lobby_row_capture(state: str):
    """A never-active capture as the REAL row adapter maps it, not hand-built.

    The lobby-billing bug lived in ``_capture_from_row``'s created_at fallback;
    ``created_at`` is NOT NULL DEFAULT now(), so a real row always carries one
    and a capture seeded directly with ``started_at=None`` exercises a state the
    broken mapping could never produce — the regression would pass unfixed."""
    from meeting_api.zaki_control.adapters import SqlAlchemyControlStore

    return SqlAlchemyControlStore._capture_from_row({
        "capture_id": "cap-1", "tenant_id": "tenant-1", "user_id": "42",
        "operation_id": "op-1", "reservation_id": "reserve-1",
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "meeting_id": "meeting-1", "state": state, "failure_code": None,
        "captured_seconds_total": 0, "max_capture_seconds": 3600,
        "start_time": None, "end_time": None,
        "created_at": datetime(2026, 7, 21, 11, 47, 13),
    })


async def test_lobby_only_capture_settles_zero_live_and_via_reconcile():
    """start_time NULL ⇒ no meter anchor ⇒ the hold releases in full (0 seconds),
    on the live stop path and on a reconcile sweep running long after the fact."""
    store = InMemoryControlStore()
    capture = _lobby_row_capture("awaiting_admission")
    assert capture.started_at is None
    store.captures[capture.capture_id] = capture
    dispatcher = _meter_dispatcher(store, datetime(2026, 7, 21, 13, 0, 0, tzinfo=timezone.utc))
    await dispatcher.record_capture_status(capture, state="failed", failure_code="join_denied")
    assert await _usage_totals(store, "cap-1") == [0]

    store = InMemoryControlStore()
    capture = _lobby_row_capture("requested")
    store.captures[capture.capture_id] = capture
    store.reconcile_meetings = [{
        "id": capture.meeting_id, "status": "failed",
        "data": {"failure_code": "join_denied"},
        "end_time": datetime(2026, 7, 21, 12, 2, 0),
    }]
    dispatcher = _meter_dispatcher(store, datetime(2026, 7, 21, 13, 0, 0, tzinfo=timezone.utc))
    assert await dispatcher.reconcile_once() == 1
    assert await _usage_totals(store, "cap-1") == [0]


async def test_reconcile_settles_the_meeting_end_not_the_reconcile_clock():
    """A 90s meeting (12:00:00 → 12:01:30) settles 90 whether the reconcile sweep
    runs one second or one hour after the end. The reconcile path used to bound
    the settlement with now(), so a late sweep settled wall-clock-at-reconcile —
    2033s billed for a 2-minute meeting."""
    start = datetime(2026, 7, 21, 12, 0, 0)   # naive-UTC, as the meetings row stores it
    end = datetime(2026, 7, 21, 12, 1, 30)
    for lateness in (timedelta(seconds=1), timedelta(hours=1)):
        store = InMemoryControlStore()
        capture = replace(_CAPTURE, state="requested", started_at=start,
                          max_capture_seconds=3600)
        store.captures[capture.capture_id] = capture
        store.reconcile_meetings = [
            {"id": capture.meeting_id, "status": "completed", "data": {}, "end_time": end}
        ]
        dispatcher = _meter_dispatcher(store, end.replace(tzinfo=timezone.utc) + lateness)
        assert await dispatcher.reconcile_once() == 1
        assert store.captures["cap-1"].state == "completed"
        assert await _usage_totals(store, "cap-1") == [90], f"lateness={lateness}"


async def test_reconcile_accepts_the_crash_recovery_feeders_isoformat_end_time():
    """Crash recovery (the capture-lease retry) rebuilds the meeting row through
    the meeting repo adapter, which isoformats every timestamp — naive-UTC, per
    the storage contract. For a crash-before-bind capture that door is the ONLY
    settlement path, and the deterministic terminal event ID makes its number
    permanent: dropping the string end re-settled wall-clock-at-retry (the
    2033s-class bug this workstream exists to fix)."""
    for end_time in ("2026-07-21T12:01:30", "2026-07-21T12:01:30Z"):
        store = InMemoryControlStore()
        capture = replace(_CAPTURE, state="requested",
                          started_at=datetime(2026, 7, 21, 12, 0, 0),
                          max_capture_seconds=3600)
        store.captures[capture.capture_id] = capture
        # The retry's clock is an hour past the meeting: the shape the Hub
        # produces when it re-leases a capture op long after a crashed spawn.
        dispatcher = _meter_dispatcher(store, datetime(2026, 7, 21, 13, 1, 30, tzinfo=timezone.utc))
        await dispatcher.reconcile_capture_lifecycle({
            "id": capture.meeting_id, "status": "completed",
            "data": {}, "end_time": end_time,
        })
        assert store.captures["cap-1"].state == "completed"
        assert await _usage_totals(store, "cap-1") == [90], f"end_time={end_time!r}"


def test_capture_cap_and_floor_still_bound_an_explicit_meeting_end():
    capture = replace(
        _CAPTURE,
        started_at=datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc),
        max_capture_seconds=3600,
    )
    current = datetime(2026, 7, 21, 12, 0, 30, tzinfo=timezone.utc)
    four_hour_end = datetime(2026, 7, 21, 16, 0, 0, tzinfo=timezone.utc)
    assert capture_seconds_at(capture, current, ended_at=four_hour_end) == 3600
    # An end that would under-report cannot beat the already-recorded total.
    floored = replace(capture, captured_seconds_total=120)
    assert capture_seconds_at(floored, current, ended_at=current) == 120
