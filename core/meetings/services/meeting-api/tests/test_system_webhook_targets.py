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
    build_system_webhook_from_env,
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
async def test_each_target_gets_the_same_body_signed_with_its_own_secret(monkeypatch, fake_redis):
    _set_env(monkeypatch, targets=BRAIN_TARGETS, secrets=BRAIN_SECRETS)
    router = _Router()
    fanout = build_system_webhook_from_env(fake_redis, transport=router)
    assert isinstance(fanout, SystemWebhookFanout)
    assert [s.name for s in fanout.sinks] == ["default", "brain"]

    results = await fanout.deliver(_envelope(), label="meeting:51")
    assert [(name, r.status) for name, r in results] == [
        ("default", "delivered"), ("brain", "delivered"),
    ]
    [legacy] = router.to(WEBAPP_URL)
    [brain] = router.to(BRAIN_URL)
    assert brain["body"] == legacy["body"]
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
    assert legacy["body"] == brain["body"]
    assert json.loads(legacy["body"])["event_type"] == "meeting.completed"
    assert verify_signature(brain["body"], brain["headers"], BRAIN_SECRET)
