"""Several operator-owned terminal targets, each isolated from the others.

The legacy target (VEXA_SYSTEM_WEBHOOK_URL + _SECRET) keeps its keys, queues and wire bytes. Each
further target (VEXA_SYSTEM_WEBHOOK_TARGETS + VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS) receives the same
envelope signed with its OWN secret, and retries / dead-letters from its OWN lists, so one failing
or slow target never delays or drops delivery to another.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.webhooks import (
    BACKOFF_SCHEDULE,
    MAX_AGE_SECONDS,
    SYSTEM_DEAD_LETTER_KEY,
    SYSTEM_PROCESSING_KEY,
    SYSTEM_RETRY_QUEUE_KEY,
    SystemWebhookFanout,
    SystemWebhookSink,
    build_envelope,
    RetryQueue,
    build_system_webhook_from_env,
    minimal_terminal_envelope,
    run_system_drain_loop,
    sign_payload,
    system_queue_keys,
    verify_signature,
)

from conftest import FakeResponse

WEBAPP_URL = "http://webapp:3000/api/hooks/meeting-completed"
WEBAPP_SECRET = "webapp-system-secret"
BRAIN_URL = "https://brain.example/hooks/vexa-meetings"
BRAIN_SECRET = "brain-system-secret"
BRAIN_QUEUE, BRAIN_PROCESSING, BRAIN_DEAD = system_queue_keys("brain")


class _Router:
    """A transport that answers per URL and records every call."""

    def __init__(self, codes=None):
        self.codes = dict(codes or {})
        self.received = []

    async def __call__(self, url, body, headers):
        code = self.codes.get(url, 200)
        self.received.append({"url": url, "body": body, "headers": dict(headers), "code": code})
        if isinstance(code, BaseException):
            raise code
        return FakeResponse(code)

    def to(self, url):
        return [item for item in self.received if item["url"] == url]


def _envelope(n=51):
    return build_envelope(
        "meeting.completed",
        {"meeting": {"id": n, "user_id": 17}},
        event_id=f"evt_{n:032d}",
    )


def _set_env(monkeypatch, *, legacy=True, targets=None, secrets=None):
    for key in (
        "VEXA_SYSTEM_WEBHOOK_URL",
        "VEXA_SYSTEM_WEBHOOK_SECRET",
        "VEXA_SYSTEM_WEBHOOK_TARGETS",
        "VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS",
        "VEXA_SYSTEM_WEBHOOK_TIMEOUT_S",
    ):
        monkeypatch.delenv(key, raising=False)
    if legacy:
        monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_URL", WEBAPP_URL)
        monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_SECRET", WEBAPP_SECRET)
        monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_ALLOW_PRIVATE_HTTP", "true")
    if targets is not None:
        monkeypatch.setenv(
            "VEXA_SYSTEM_WEBHOOK_TARGETS",
            targets if isinstance(targets, str) else json.dumps(targets),
        )
    if secrets is not None:
        monkeypatch.setenv(
            "VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS",
            secrets if isinstance(secrets, str) else json.dumps(secrets),
        )


BRAIN_TARGETS = [{"name": "brain", "url": BRAIN_URL}]
BRAIN_SECRETS = {"brain": BRAIN_SECRET}


# ── 1. the legacy target is unchanged ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_only_config_builds_the_same_single_sink_with_the_same_keys(
    monkeypatch, fake_redis,
):
    _set_env(monkeypatch)
    router = _Router()
    sink = build_system_webhook_from_env(fake_redis, transport=router)
    assert type(sink) is SystemWebhookSink
    assert sink.name == "default"
    assert (sink.retry_key, sink.processing_key, sink.dead_letter_key) == (
        "webhook:system:retry_queue",
        "webhook:system:retry_processing",
        "webhook:system:dead_letter",
    )
    assert (SYSTEM_RETRY_QUEUE_KEY, SYSTEM_PROCESSING_KEY, SYSTEM_DEAD_LETTER_KEY) == (
        sink.retry_key, sink.processing_key, sink.dead_letter_key,
    )


@pytest.mark.asyncio
async def test_legacy_target_wire_bytes_and_headers_are_unchanged(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router()
    fanout = build_system_webhook_from_env(fake_redis, transport=router)
    envelope = _envelope()
    await fanout.deliver(envelope, label="meeting:51")

    [legacy] = router.to(WEBAPP_URL)
    # The body is exactly json.dumps(envelope), as before this change.
    assert legacy["body"] == json.dumps(envelope).encode()
    headers = legacy["headers"]
    assert set(headers) == {
        "Content-Type", "Authorization", "X-Webhook-Signature", "X-Webhook-Timestamp",
    }
    assert headers["Authorization"] == f"Bearer {WEBAPP_SECRET}"
    assert headers["X-Webhook-Signature"] == sign_payload(
        legacy["body"], WEBAPP_SECRET, headers["X-Webhook-Timestamp"],
    )


def test_nothing_configured_is_still_no_sink(monkeypatch, fake_redis):
    _set_env(monkeypatch, legacy=False)
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_TIMEOUT_S", "NaN")  # ignored when nothing is configured
    assert build_system_webhook_from_env(fake_redis, transport=_Router()) is None


# ── 2. each target its own secret ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_target_gets_its_body_signed_with_its_own_secret(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router()
    fanout = build_system_webhook_from_env(fake_redis, transport=router)
    assert isinstance(fanout, SystemWebhookFanout)
    assert [s.name for s in fanout.sinks] == ["default", "brain"]

    envelope = _envelope()
    results = await fanout.deliver(envelope, label="meeting:51")
    assert [(name, r.status) for name, r in results] == [
        ("default", "delivered"), ("brain", "delivered"),
    ]
    [legacy] = router.to(WEBAPP_URL)
    [brain] = router.to(BRAIN_URL)
    assert brain["body"] == json.dumps(minimal_terminal_envelope(envelope)).encode()
    assert json.loads(brain["body"])["event_id"] == "evt_" + "0" * 30 + "51"
    assert verify_signature(brain["body"], brain["headers"], BRAIN_SECRET)
    assert not verify_signature(brain["body"], brain["headers"], WEBAPP_SECRET)
    assert verify_signature(legacy["body"], legacy["headers"], WEBAPP_SECRET)
    assert not verify_signature(legacy["body"], legacy["headers"], BRAIN_SECRET)


def test_targets_without_legacy_pair_are_allowed(monkeypatch, fake_redis):
    _set_env(monkeypatch, legacy=False, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    fanout = build_system_webhook_from_env(fake_redis, transport=_Router())
    assert [s.name for s in fanout.sinks] == ["brain"]


@pytest.mark.parametrize(
    "targets,secrets,match",
    [
        (BRAIN_TARGETS, None, "configured together"),
        (None, BRAIN_SECRETS, "configured together"),
        ("not json", BRAIN_SECRETS, "JSON list"),
        (BRAIN_TARGETS, "not json", "JSON object"),
        ([], BRAIN_SECRETS, "non-empty"),
        ([{"name": "Brain", "url": BRAIN_URL}], {"Brain": "x"}, "name must match"),
        ([{"name": "default", "url": BRAIN_URL}], {"default": "x"}, "reserved"),
        ([{"name": "brain"}], BRAIN_SECRETS, "needs a url"),
        ([{"name": "brain", "url": BRAIN_URL, "secret": "x"}], BRAIN_SECRETS, "unknown field"),
        (BRAIN_TARGETS * 2, BRAIN_SECRETS, "unique"),
        (BRAIN_TARGETS, {"brain": "s", "other": "t"}, "exactly the targets"),
        (BRAIN_TARGETS, {"brain": ""}, "non-empty secret"),
        ([{"name": "brain", "url": BRAIN_URL, "timeout_s": 0}], BRAIN_SECRETS, "timeout_s"),
        ([{"name": "brain", "url": BRAIN_URL, "allow_private_http": "yes"}], BRAIN_SECRETS, "boolean"),
    ],
)
def test_malformed_target_config_refuses_boot(monkeypatch, fake_redis, targets, secrets, match):
    _set_env(monkeypatch, targets=targets, secrets=secrets)
    with pytest.raises(RuntimeError, match=match):
        build_system_webhook_from_env(fake_redis, transport=_Router())


def test_a_secret_shared_between_targets_refuses_boot(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets={"brain": WEBAPP_SECRET})
    with pytest.raises(RuntimeError, match="its own secret"):
        build_system_webhook_from_env(fake_redis, transport=_Router())


def test_config_errors_never_echo_a_secret(monkeypatch, fake_redis):
    secret_text = '{"brain": "super-secret-value"'  # truncated JSON
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=secret_text)
    with pytest.raises(RuntimeError) as info:
        build_system_webhook_from_env(fake_redis, transport=_Router())
    assert "super-secret-value" not in str(info.value)
    assert info.value.__cause__ is None


def test_named_target_keeps_the_destination_rules(monkeypatch, fake_redis):
    _set_env(
        monkeypatch,
        targets=[{"name": "brain", "url": "http://brain.example/hooks/vexa-meetings"}],
        secrets=BRAIN_SECRETS,
    )
    with pytest.raises(ValueError, match="private HTTP"):
        build_system_webhook_from_env(fake_redis, transport=_Router())


# ── 3. a failing or slow target never delays or drops another ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_target_queues_only_in_its_own_lists(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router({BRAIN_URL: 503})
    fanout = build_system_webhook_from_env(fake_redis, transport=router)

    results = dict(await fanout.deliver(_envelope(), label="meeting:51"))
    assert results["default"].status == "delivered"
    assert results["brain"].status == "queued"
    assert await fake_redis.llen(SYSTEM_RETRY_QUEUE_KEY) == 0
    assert await fake_redis.llen(BRAIN_QUEUE) == 1
    queued = json.loads((await fake_redis.lrange(BRAIN_QUEUE, 0, -1))[0])
    assert queued["url"] == BRAIN_URL
    assert queued["webhook_secret"] == BRAIN_SECRET

    # The brain recovers: its own drain redelivers the SAME event, signed with ITS secret;
    # the webapp is not re-sent anything.
    router.codes[BRAIN_URL] = 200
    processed = await fanout.drain(now=time.time() + BACKOFF_SCHEDULE[0] + 1)
    assert processed == 1
    assert await fanout.retry_depth() == 0
    assert len(router.to(WEBAPP_URL)) == 1
    redelivered = router.to(BRAIN_URL)[-1]
    assert json.loads(redelivered["body"])["event_id"] == "evt_" + "0" * 30 + "51"
    assert verify_signature(redelivered["body"], redelivered["headers"], BRAIN_SECRET)


@pytest.mark.asyncio
async def test_an_exhausted_target_dead_letters_only_into_its_own_list(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router({BRAIN_URL: 503, WEBAPP_URL: 503})
    fanout = build_system_webhook_from_env(fake_redis, transport=router)
    t0 = time.time()
    await fanout.deliver(_envelope(), label="meeting:51")
    assert await fake_redis.llen(SYSTEM_RETRY_QUEUE_KEY) == 1
    assert await fake_redis.llen(BRAIN_QUEUE) == 1

    # Only the webapp recovers; the brain ages out.
    router.codes[WEBAPP_URL] = 200
    await fanout.drain(now=t0 + BACKOFF_SCHEDULE[0] + 1)
    assert await fake_redis.llen(SYSTEM_RETRY_QUEUE_KEY) == 0
    await fanout.drain(now=t0 + MAX_AGE_SECONDS + 1)
    assert await fake_redis.llen(BRAIN_DEAD) == 1
    [dead] = await fake_redis.lrange(BRAIN_DEAD, 0, -1)
    assert set(json.loads(dead)["payload"]["data"]) == {"meeting"}
    assert await fake_redis.llen(SYSTEM_DEAD_LETTER_KEY) == 0
    assert await fake_redis.llen(BRAIN_QUEUE) == 0


@pytest.mark.asyncio
async def test_a_slow_target_does_not_delay_another():
    release = asyncio.Event()
    webapp_received = asyncio.Event()
    calls = []

    async def transport(url, body, headers):
        calls.append(url)
        if url == BRAIN_URL:
            await release.wait()  # a broker that hangs until its timeout
        else:
            webapp_received.set()
        return FakeResponse(200)

    fanout = SystemWebhookFanout([
        SystemWebhookSink(
            url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=transport,
            allow_private_http=True,
        ),
        SystemWebhookSink(
            name="brain", url=BRAIN_URL, secret=BRAIN_SECRET, transport=transport,
        ),
    ])
    task = asyncio.create_task(fanout.deliver(_envelope(), label="meeting:51"))
    await asyncio.wait_for(webapp_received.wait(), timeout=1)
    assert not task.done()  # the brain is still hanging; the webapp already has its message
    release.set()
    results = dict(await asyncio.wait_for(task, timeout=1))
    assert results["default"].status == results["brain"].status == "delivered"


@pytest.mark.asyncio
async def test_a_target_that_raises_is_contained():
    router = _Router()

    class _Broken(SystemWebhookSink):
        async def deliver(self, envelope, *, label=""):
            raise RuntimeError("boom")

    fanout = SystemWebhookFanout([
        _Broken(name="brain", url=BRAIN_URL, secret=BRAIN_SECRET, transport=router),
        SystemWebhookSink(
            url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=router, allow_private_http=True,
        ),
    ])
    results = dict(await fanout.deliver(_envelope(), label="meeting:51"))
    assert results["brain"].status == "failed"
    assert results["brain"].error == "RuntimeError"
    assert results["default"].status == "delivered"
    assert len(router.to(WEBAPP_URL)) == 1


@pytest.mark.asyncio
async def test_one_targets_drain_fault_does_not_stop_anothers_drain(fake_redis):
    router = _Router({WEBAPP_URL: 503})

    class _BrokenDrain(SystemWebhookSink):
        async def drain(self, *, now=None):
            raise RuntimeError("drain boom")

    fanout = SystemWebhookFanout([
        _BrokenDrain(name="brain", url=BRAIN_URL, secret=BRAIN_SECRET, transport=router,
                     redis=fake_redis),
        SystemWebhookSink(url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=router,
                          redis=fake_redis, allow_private_http=True),
    ])
    t0 = time.time()
    await fanout.deliver(_envelope(), label="meeting:51")
    router.codes[WEBAPP_URL] = 200
    with pytest.raises(RuntimeError, match="drain boom"):
        await fanout.drain(now=t0 + BACKOFF_SCHEDULE[0] + 1)
    assert await fake_redis.llen(SYSTEM_RETRY_QUEUE_KEY) == 0  # the webapp drained anyway


# ── 4. through the lifecycle callback ─────────────────────────────────────────────────────────


def test_lifecycle_completion_reaches_every_target_once():
    repo = InMemoryMeetingRepo()
    meeting = asyncio.run(repo.create_meeting(
        user_id=17, platform="google_meet", native_meeting_id="targets-meeting", data={},
    ))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid="sess-targets"))
    router = _Router({BRAIN_URL: 503})
    fanout = SystemWebhookFanout([
        SystemWebhookSink(url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=router,
                          allow_private_http=True),
        SystemWebhookSink(name="brain", url=BRAIN_URL, secret=BRAIN_SECRET, transport=router),
    ])
    client = TestClient(
        create_app(open_callbacks=True, meeting_repo=repo, system_webhook_sink=fanout),
    )
    for status, ts in (("joining", "10:00"), ("active", "10:01"), ("completed", "10:02")):
        body = {
            "connection_id": "sess-targets",
            "status": status,
            "timestamp": f"2026-07-29T{ts}:00.000Z",
        }
        if status == "completed":
            body["completion_reason"] = "stopped"
        response = client.post("/bots/internal/callback/lifecycle", json=body)
        assert response.status_code == 200, response.text

    [legacy] = router.to(WEBAPP_URL)
    [brain] = router.to(BRAIN_URL)
    legacy_body = json.loads(legacy["body"])
    brain_body = json.loads(brain["body"])
    assert legacy_body["event_type"] == brain_body["event_type"] == "meeting.completed"
    assert legacy_body["event_id"] == brain_body["event_id"]
    assert "data" in legacy_body["data"]["meeting"]  # the web app keeps the full body
    assert brain_body == minimal_terminal_envelope(legacy_body)
    assert verify_signature(brain["body"], brain["headers"], BRAIN_SECRET)


# ── 5. one target's slow sweep never delays another's retries (own loop per target) ───────────


@pytest.mark.asyncio
async def test_a_dead_targets_sweep_never_delays_anothers_retries(fake_redis):
    hang = asyncio.Event()  # never set: every brain POST hangs like a dead endpoint
    calls = []

    async def transport(url, body, headers):
        calls.append(url)
        if url == BRAIN_URL:
            await hang.wait()
        return FakeResponse(200)

    webapp = SystemWebhookSink(url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=transport,
                               redis=fake_redis, allow_private_http=True)
    brain = SystemWebhookSink(name="brain", url=BRAIN_URL, secret=BRAIN_SECRET,
                              transport=transport, redis=fake_redis)
    due = time.time() - BACKOFF_SCHEDULE[0] - 1  # enqueued long enough ago to be due now
    await RetryQueue(fake_redis, key=webapp.retry_key).enqueue(
        url=WEBAPP_URL, envelope=_envelope(1), webhook_secret=WEBAPP_SECRET, now=due,
    )
    for n in range(6):
        await RetryQueue(fake_redis, key=brain.retry_key).enqueue(
            url=BRAIN_URL, envelope=_envelope(10 + n), webhook_secret=BRAIN_SECRET, now=due,
        )

    tasks = [
        asyncio.create_task(run_system_drain_loop(brain, interval=0.01)),
        asyncio.create_task(run_system_drain_loop(webapp, interval=0.01)),
    ]
    try:
        async def _webapp_drained():
            while await fake_redis.llen(webapp.retry_key) or WEBAPP_URL not in calls:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_webapp_drained(), timeout=2)
        assert calls.count(BRAIN_URL) == 1  # the brain sweep is still stuck on its first entry
        assert await fake_redis.llen(brain.processing_key) == 1
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ── 6. system sends never hold up the customer webhook ────────────────────────────────────────


class _CustomerSink:
    def __init__(self):
        self._delivered = None
        self.events = []

    @property
    def delivered(self):
        # Created on first use inside the app's running loop, by whichever side gets there first.
        if self._delivered is None:
            self._delivered = asyncio.Event()
        return self._delivered

    async def deliver(self, url, envelope, secret=None, *, events_config=None, label="", **_):
        from meeting_api.webhooks import DeliveryResult

        self.events.append(envelope["event_type"])
        if envelope["event_type"] == "meeting.completed":
            self.delivered.set()
        return DeliveryResult(status="delivered", status_code=200)


class _SlowSystem:
    """Holds its send open until the customer's meeting.completed has gone (or 2 s pass)."""

    def __init__(self, customer):
        self.customer = customer
        self.released_by_customer = None

    async def deliver(self, envelope, *, label=""):
        try:
            await asyncio.wait_for(self.customer.delivered.wait(), timeout=2)
            self.released_by_customer = True
        except asyncio.TimeoutError:
            self.released_by_customer = False
        return None


def test_a_slow_system_target_does_not_hold_up_the_customer_webhook():
    repo = InMemoryMeetingRepo()
    meeting = asyncio.run(repo.create_meeting(
        user_id=17, platform="google_meet", native_meeting_id="customer-first",
        data={"webhook_url": "https://customer.example/hook", "webhook_secret": "c-secret",
              "webhook_events": {"meeting.completed": True}},
    ))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid="sess-cust"))
    customer = _CustomerSink()
    system = _SlowSystem(customer)
    client = TestClient(create_app(
        open_callbacks=True, meeting_repo=repo,
        webhook_sink=customer, system_webhook_sink=system,
    ))
    for status, ts in (("joining", "10:00"), ("active", "10:01"), ("completed", "10:02")):
        body = {"connection_id": "sess-cust", "status": status,
                "timestamp": f"2026-07-29T{ts}:00.000Z"}
        if status == "completed":
            body["completion_reason"] = "stopped"
        response = client.post("/bots/internal/callback/lifecycle", json=body)
        assert response.status_code == 200, response.text

    assert "meeting.completed" in customer.events
    # Under the old order the system send ran first and the customer waited 2 s behind it.
    assert system.released_by_customer is True


# ── 7. per-target field allow-list (card 01a212de) ────────────────────────────────────────────

_SENSITIVE = {
    "share_grants": [{"token_hash": "sha256-SHARE-GRANT-HASH", "viewer_email": "viewer@example.com"}],
    "browser_session": {"profile_path": "/var/lib/vexa/BROWSER-SESSION-PATH"},
    "viewers": ["viewer2@example.com"],
    "calendar_invite": "BEGIN:VCALENDAR RAW-INVITE",
    "bot_logs": ["BOT-LOG-LINE"],
    "join_screenshot": "s3://bucket/JOIN-SCREENSHOT.png",
    "webhook_url": "https://customer.example/hook",
}
_SENSITIVE_MARKERS = (
    "SHARE-GRANT-HASH", "viewer@example.com", "viewer2@example.com", "BROWSER-SESSION-PATH",
    "RAW-INVITE", "BOT-LOG-LINE", "JOIN-SCREENSHOT", "customer.example",
    "completion_reason", "constructed_meeting_url", "bot_outcome",
)


def _rich_envelope(event_type="meeting.completed"):
    return build_envelope(
        event_type,
        {
            "meeting": {
                "id": 77,
                "user_id": 17,
                "platform": "google_meet",
                "native_meeting_id": "abc-defg-hij",
                "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
                "status": "completed" if event_type == "meeting.completed" else "failed",
                "completion_reason": "stopped",
                "failure_stage": None,
                "billing_treatment": "metered",
                "service_provenance": {
                    "bot_admitted_at": "2026-07-29T10:01:00Z",
                    "bot_departed_at": "2026-07-29T10:12:00Z",
                    "bot_outcome": "served",
                    "transcription_provider": "vexa",
                    "transcription_outcome": "served",
                    "lifecycle_contract_version": "2026-07-28",
                    "extra_future_field": "DROP-ME",
                },
                "start_time": "2026-07-29T10:00:00+00:00",
                "end_time": "2026-07-29T10:12:00+00:00",
                "data": dict(_SENSITIVE),
                "created_at": "2026-07-29T09:59:00+00:00",
                "updated_at": "2026-07-29T10:12:00+00:00",
            },
        },
        event_id="evt_" + "7" * 32,
    )


def _golden_minimal(envelope):
    return {
        "event_id": "evt_" + "7" * 32,
        "event_type": envelope["event_type"],
        "api_version": envelope["api_version"],
        "created_at": envelope["created_at"],
        "data": {
            "meeting": {
                "id": 77,
                "user_id": 17,
                "platform": "google_meet",
                "native_meeting_id": "abc-defg-hij",
                "status": envelope["data"]["meeting"]["status"],
                "start_time": "2026-07-29T10:00:00+00:00",
                "end_time": "2026-07-29T10:12:00+00:00",
                "billing_treatment": "metered",
                "service_provenance": {
                    "bot_admitted_at": "2026-07-29T10:01:00Z",
                    "bot_departed_at": "2026-07-29T10:12:00Z",
                    "transcription_provider": "vexa",
                    "transcription_outcome": "served",
                    "lifecycle_contract_version": "2026-07-28",
                },
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["meeting.completed", "bot.failed"])
async def test_golden_bodies_per_target(monkeypatch, fake_redis, event_type):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router()
    fanout = build_system_webhook_from_env(fake_redis, transport=router)
    envelope = _rich_envelope(event_type)
    await fanout.deliver(envelope, label="meeting:77")

    [legacy] = router.to(WEBAPP_URL)
    [brain] = router.to(BRAIN_URL)
    # default target: today's body, byte for byte (meeting.data included — the web app's contract)
    assert legacy["body"] == json.dumps(envelope).encode()
    # named target: exactly the allow-list, nothing else
    assert json.loads(brain["body"]) == _golden_minimal(envelope)
    assert brain["body"] == json.dumps(_golden_minimal(envelope)).encode()
    for marker in _SENSITIVE_MARKERS + ("DROP-ME",):
        assert marker.encode() not in brain["body"], marker
    assert verify_signature(brain["body"], brain["headers"], BRAIN_SECRET)


@pytest.mark.asyncio
async def test_named_targets_retry_queue_holds_only_the_allow_list(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router({BRAIN_URL: 503})
    fanout = build_system_webhook_from_env(fake_redis, transport=router)
    envelope = _rich_envelope()
    await fanout.deliver(envelope, label="meeting:77")
    [raw] = await fake_redis.lrange(BRAIN_QUEUE, 0, -1)
    for marker in _SENSITIVE_MARKERS:
        assert marker not in raw, marker
    assert json.loads(raw)["payload"] == _golden_minimal(envelope)

    router.codes[BRAIN_URL] = 200
    await fanout.drain(now=time.time() + BACKOFF_SCHEDULE[0] + 1)
    redelivered = router.to(BRAIN_URL)[-1]["body"]
    assert json.loads(redelivered) == _golden_minimal(envelope)


def test_structured_values_are_dropped_not_passed_through():
    envelope = _rich_envelope()
    envelope["data"]["meeting"]["status"] = {"nested": "BOT-LOG-LINE"}
    envelope["data"]["meeting"]["user_id"] = True
    envelope["data"]["meeting"]["service_provenance"]["transcription_outcome"] = ["x"]
    projected = minimal_terminal_envelope(envelope)
    meeting = projected["data"]["meeting"]
    assert meeting["status"] is None
    assert meeting["user_id"] is None
    assert meeting["service_provenance"]["transcription_outcome"] is None
    assert "BOT-LOG-LINE" not in json.dumps(projected)


def test_unresolved_provenance_stays_null():
    envelope = _rich_envelope()
    envelope["data"]["meeting"]["service_provenance"] = None
    assert minimal_terminal_envelope(envelope)["data"]["meeting"]["service_provenance"] is None


def test_lifecycle_path_never_sends_meeting_data_to_a_named_target():
    repo = InMemoryMeetingRepo()
    meeting = asyncio.run(repo.create_meeting(
        user_id=17, platform="google_meet", native_meeting_id="leak-check",
        data=dict(_SENSITIVE),
    ))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid="sess-leak"))
    router = _Router()
    fanout = SystemWebhookFanout([
        SystemWebhookSink(url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=router,
                          allow_private_http=True),
        SystemWebhookSink(name="brain", url=BRAIN_URL, secret=BRAIN_SECRET, transport=router),
    ])
    client = TestClient(
        create_app(open_callbacks=True, meeting_repo=repo, system_webhook_sink=fanout),
    )
    for status, ts in (("joining", "10:00"), ("active", "10:01"), ("completed", "10:02")):
        body = {"connection_id": "sess-leak", "status": status,
                "timestamp": f"2026-07-29T{ts}:00.000Z"}
        if status == "completed":
            body["completion_reason"] = "stopped"
        assert client.post("/bots/internal/callback/lifecycle", json=body).status_code == 200
    [legacy] = router.to(WEBAPP_URL)
    [brain] = router.to(BRAIN_URL)
    assert b"BOT-LOG-LINE" in legacy["body"]  # unchanged web app body
    for marker in ("SHARE-GRANT-HASH", "viewer@example.com", "BROWSER-SESSION-PATH",
                   "RAW-INVITE", "BOT-LOG-LINE", "JOIN-SCREENSHOT", "customer.example"):
        assert marker.encode() not in brain["body"], marker
    meeting_block = json.loads(brain["body"])["data"]["meeting"]
    assert set(meeting_block) == {
        "id", "user_id", "platform", "native_meeting_id", "status", "start_time",
        "end_time", "billing_treatment", "service_provenance",
    }


@pytest.mark.asyncio
async def test_a_full_entry_left_by_an_older_build_is_reprojected_before_any_use(fake_redis):
    """An entry queued before the allow-list existed carries full meeting.data. The named target's
    drain must project it again before it is sent, re-queued or dead-lettered."""
    router = _Router()
    brain = SystemWebhookSink(name="brain", url=BRAIN_URL, secret=BRAIN_SECRET,
                              transport=router, redis=fake_redis)
    envelope = _rich_envelope()
    t0 = time.time()
    queue = RetryQueue(fake_redis, key=brain.retry_key)
    await queue.enqueue(url=BRAIN_URL, envelope=envelope, webhook_secret=BRAIN_SECRET,
                        now=t0 - BACKOFF_SCHEDULE[0] - 1)            # due: delivered
    await queue.enqueue(url=BRAIN_URL, envelope=envelope, webhook_secret=BRAIN_SECRET, now=t0)
    await queue.enqueue(url=BRAIN_URL, envelope=envelope, webhook_secret=BRAIN_SECRET,
                        now=t0 - MAX_AGE_SECONDS - 10)               # aged out: dead-lettered

    await brain.drain(now=t0)

    [sent] = router.to(BRAIN_URL)
    assert json.loads(sent["body"]) == _golden_minimal(envelope)
    assert verify_signature(sent["body"], sent["headers"], BRAIN_SECRET)
    stored = await fake_redis.lrange(brain.retry_key, 0, -1) + await fake_redis.lrange(
        brain.dead_letter_key, 0, -1)
    assert len(stored) == 2
    for raw in stored:
        for marker in _SENSITIVE_MARKERS:
            assert marker not in raw, marker
        assert json.loads(raw)["payload"] == _golden_minimal(envelope)


@pytest.mark.asyncio
async def test_the_default_target_drain_still_resends_its_stored_body_unchanged(fake_redis):
    router = _Router()
    webapp = SystemWebhookSink(url=WEBAPP_URL, secret=WEBAPP_SECRET, transport=router,
                               redis=fake_redis, allow_private_http=True)
    envelope = _rich_envelope()
    await RetryQueue(fake_redis, key=webapp.retry_key).enqueue(
        url=WEBAPP_URL, envelope=envelope, webhook_secret=WEBAPP_SECRET,
        now=time.time() - BACKOFF_SCHEDULE[0] - 1,
    )
    await webapp.drain()
    [sent] = router.to(WEBAPP_URL)
    assert sent["body"] == json.dumps(envelope).encode()
