"""Adversarial seam test for webhook delivery + exponential-backoff retry.

Pins down the EXACT reliability contract of `meeting_api.webhooks` against a fake
transport we fully control (chosen status / raised timeout / connection error /
recover-after-N) and a real `fakeredis`-backed `RetryQueue`:

  (a) the backoff delays follow the documented exponential schedule + cap;
  (b) 5xx / 429 / timeout / connection-refused retry, but a 4xx (non-429) does NOT;
  (c) after the schedule is exhausted the envelope is dropped (not infinite-looped);
  (d) the HMAC signature is computed over `<ts>.<body>` and verifies with the secret;
  (e) only the per-client's configured events are delivered (others suppressed);
  (f) the SSRF guard blocks localhost / 127. / 10. / 172.16. / 192.168. / 169.254. /
      ::1 / internal hostnames, and allows public hosts;
  (g) a successful (2xx) delivery is never re-delivered.

Where the implementation has a real reliability gap, the test is marked
`xfail(strict=True, reason="BUG: ...")` with an expected-vs-actual message, so the gap
is recorded and a future fix flips it green (a regression then re-reds it).

Run: cd core/meetings/services/meeting-api && python -m pytest tests/test_webhook_seam.py -q
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from meeting_api.webhooks import (
    BACKOFF_SCHEDULE,
    MAX_AGE_SECONDS,
    RETRY_QUEUE_KEY,
    DeliveryResult,
    RetryQueue,
    SSRFError,
    WebhookSink,
    build_envelope,
    build_headers,
    drain_retry_queue,
    is_event_enabled,
    sign_payload,
    validate_webhook_url,
)
from meeting_api.webhooks.retry import DEAD_LETTER_KEY

SECRET = "whsec_seam_secret"
URL = "https://hooks.example.com/vexa"

# A resolver stub so the SSRF guard never touches DNS for our public test host.
_PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731

# A per-client config: subscribed to meeting.completed, explicitly OFF for status_change.
SUBSCRIBED = {"meeting.completed": True, "meeting.status_change": False}


# ── a scripted transport we fully control ────────────────────────────────────────────


class TimeoutErr(Exception):
    """Stand-in for httpx.TimeoutException."""


class ConnErr(Exception):
    """Stand-in for httpx.ConnectError (connection refused)."""


@dataclass
class ScriptedTransport:
    """A fake transport. Each call pops the next scripted action:

    * an int N      → respond HTTP N
    * an Exception  → raise it (timeout / connection error)
    Falls back to `default_code` once the script is exhausted.
    Records every (url, body, headers) so the test can recompute the HMAC.
    """

    script: List[Any] = field(default_factory=list)
    default_code: int = 200
    received: List[Dict[str, Any]] = field(default_factory=list)
    calls: int = 0

    async def __call__(self, url: str, body: bytes, headers: Dict[str, str]):
        self.calls += 1
        action = self.script.pop(0) if self.script else self.default_code
        self.received.append({"url": url, "body": body, "headers": dict(headers)})
        if isinstance(action, Exception):
            raise action
        return _Resp(action)


@dataclass
class _Resp:
    status_code: int


# A locally-recomputing verifier independent of the implementation's verify_signature,
# so test (d) catches a "signs the wrong bytes" bug rather than agreeing with it.
def _independent_verify(body: bytes, headers: Dict[str, str], secret: str) -> bool:
    import hashlib
    import hmac

    sig = headers.get("X-Webhook-Signature")
    ts = headers.get("X-Webhook-Timestamp")
    if not sig or not ts:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


# ════════════════════════════════════════════════════════════════════════════════════
# (d) HMAC signature is computed over `<ts>.<body>` and verifies with the secret
# ════════════════════════════════════════════════════════════════════════════════════


async def test_delivered_body_hmac_verifies_independently():
    t = ScriptedTransport(default_code=200)
    sink = WebhookSink(transport=t, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "delivered" and res.status_code == 200
    rec = t.received[0]
    # Independent recompute (NOT the impl's verify_signature) over ts.body.
    assert _independent_verify(rec["body"], rec["headers"], SECRET), (
        "HMAC must be sha256 over '<X-Webhook-Timestamp>.' + raw body"
    )


def test_signature_signs_ts_dot_body_exactly():
    body = b'{"k":"v"}'
    ts = "1771401720"
    headers = build_headers(SECRET, body, timestamp=ts)
    assert headers["X-Webhook-Signature"] == sign_payload(body, SECRET, ts)
    # Sanity: the signed content is `<ts>.` + body, NOT body alone and NOT `<ts>body`.
    import hashlib
    import hmac

    over_body_only = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-Webhook-Signature"] != over_body_only, "must include the ts prefix"


def test_no_signature_without_secret():
    headers = build_headers(None, b"{}")
    assert "X-Webhook-Signature" not in headers
    assert "X-Webhook-Timestamp" not in headers
    assert headers["Content-Type"] == "application/json"


# ════════════════════════════════════════════════════════════════════════════════════
# (e) only the per-client's configured events are delivered
# ════════════════════════════════════════════════════════════════════════════════════


async def test_unsubscribed_event_suppressed_no_http():
    t = ScriptedTransport()
    sink = WebhookSink(transport=t, resolver=_PUBLIC)
    env = build_envelope("meeting.status_change", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "suppressed"
    assert t.calls == 0, "a suppressed event must not touch the transport"


async def test_subscribed_event_delivered():
    t = ScriptedTransport(default_code=200)
    sink = WebhookSink(transport=t, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "delivered"
    assert t.calls == 1


def test_event_filter_defaults_to_completed_only():
    # No config → only the default set (meeting.completed) fires.
    assert is_event_enabled(None, "meeting.completed") is True
    assert is_event_enabled(None, "meeting.status_change") is False
    assert is_event_enabled({}, "meeting.status_change") is False
    # Explicit per-event flag wins over the default.
    assert is_event_enabled({"meeting.status_change": True}, "meeting.status_change") is True
    assert is_event_enabled({"meeting.completed": False}, "meeting.completed") is False


async def test_system_scope_bypasses_filter():
    t = ScriptedTransport(default_code=200)
    sink = WebhookSink(transport=t, resolver=_PUBLIC)
    env = build_envelope("meeting.status_change", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, scope="system", events_config=None)
    assert res.status == "delivered"
    assert t.calls == 1


# ════════════════════════════════════════════════════════════════════════════════════
# (f) SSRF guard coverage
# ════════════════════════════════════════════════════════════════════════════════════

# DNS resolver stub that maps any name to loopback (simulates a rebinding attacker).
_LOOPBACK = lambda host: ["127.0.0.1"]  # noqa: E731


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/h",
        "http://localhost:8080/h",
        "https://127.0.0.1/h",
        "http://127.0.0.1:9000/x",
        "http://127.1.2.3/h",            # 127/8 — all loopback
        "http://0.0.0.0/h",              # 0/8
        "http://10.0.0.5/h",             # 10/8 private
        "http://10.255.255.255/h",       # 10/8 boundary
        "http://172.16.0.1/h",           # 172.16/12 private
        "http://172.31.255.254/h",       # 172.16/12 boundary
        "http://192.168.1.10/h",         # 192.168/16 private
        "http://169.254.169.254/latest", # cloud metadata, link-local
        "http://169.254.0.1/h",          # link-local boundary
        "https://[::1]/h",               # ipv6 loopback
        "http://redis/h",                # internal docker service
        "http://meeting-api/internal",   # internal docker service
        "http://metadata.google.internal/",
        "ftp://example.com/h",           # non-http scheme
        "file:///etc/passwd",            # non-http scheme
        "https:///nohost",               # missing hostname
    ],
)
def test_ssrf_blocks(url):
    with pytest.raises(SSRFError):
        validate_webhook_url(url, resolver=_LOOPBACK)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/vexa",
        "http://api.customer.io/webhooks/123",
        "https://93.184.216.34/h",  # literal public IP
        "http://172.32.0.1/h",      # JUST outside 172.16/12 → public
        "http://11.0.0.1/h",        # JUST outside 10/8 → public
    ],
)
def test_ssrf_allows_public(url):
    # WH2: the guard now returns a PinnedURL (a connection-safe handle), not a bare hostname
    # string. Its raw URL value is preserved (Host/SNI), so .url round-trips the original.
    out = validate_webhook_url(url, resolver=_PUBLIC)
    assert out.url == url
    assert out.pinned_ips, "a valid URL must carry the resolved+validated pinned IP(s)"


def test_ssrf_dns_rebinding_to_private_blocked():
    with pytest.raises(SSRFError):
        validate_webhook_url("https://evil.example.com/h", resolver=lambda h: ["10.1.2.3"])


def test_ssrf_unresolvable_blocked():
    with pytest.raises(SSRFError):
        validate_webhook_url("https://nope.invalid/h", resolver=lambda h: [])


async def test_sink_blocked_url_never_touches_transport():
    t = ScriptedTransport()
    sink = WebhookSink(transport=t, resolver=_LOOPBACK)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver("http://localhost/h", env, SECRET, events_config={"meeting.completed": True})
    assert res.status == "blocked"
    assert t.calls == 0


def test_ssrf_toctou_connect_time_pinning_present():
    # WH2 FIXED: the guard no longer returns a bare re-resolvable hostname URL string. It
    # returns a PinnedURL (a connection-safe handle) carrying the resolved-and-validated IP(s),
    # and the production transport (build_pinned_transport) re-resolves + re-validates + dials
    # the validated IP at connect time — so the submit→connect DNS-rebinding window is closed.
    out = validate_webhook_url("https://hooks.example.com/vexa", resolver=_PUBLIC)
    assert out != "https://hooks.example.com/vexa", (
        "expected guard to return a pinned IP/connection-safe handle, not the original "
        f"hostname URL; got {out!r}"
    )
    # The handle carries the pinned IP the transport must dial (not a re-resolvable hostname).
    assert out.pinned_ips == ["93.184.216.34"], "guard must pin the resolved+validated IP(s)"
    assert out.url == "https://hooks.example.com/vexa", "raw URL preserved for Host/SNI"


async def test_pinned_transport_revalidates_and_pins_at_connect():
    """WH2 (connect half): the pinned transport re-resolves + re-validates the host at the
    moment it dials and PINS to a validated IP — closing the submit→connect rebinding window.

    Drives it with an inner transport that records the actual dialled URL + extensions, so we
    can prove (a) a host that rebinds to loopback BETWEEN submit and connect is rejected at
    connect, and (b) a public host is dialled at its validated IP with Host/SNI preserved.
    """
    import httpx

    from meeting_api.webhooks.ssrf import SSRFError as _SSRF
    from meeting_api.webhooks.ssrf import build_pinned_transport

    dialled: List[httpx.Request] = []

    class _Recorder(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            dialled.append(request)
            return httpx.Response(200, request=request)

    # (a) A host that the connect-time resolver now maps to loopback (a rebind) is REJECTED
    # at connect — the inner transport is never dialled.
    pinned = build_pinned_transport(_Recorder(), resolver=_LOOPBACK)
    async with httpx.AsyncClient(transport=pinned) as client:
        with pytest.raises(_SSRF):
            await client.post("https://rebind.example.com/hook", content=b"{}")
    assert dialled == [], "a connect-time rebind to loopback must never reach the socket"

    # (b) A public host is dialled at its validated IP, with the original Host + TLS SNI kept.
    pinned_ok = build_pinned_transport(_Recorder(), resolver=_PUBLIC)
    async with httpx.AsyncClient(transport=pinned_ok) as client:
        resp = await client.post("https://hooks.example.com/vexa", content=b"{}")
    assert resp.status_code == 200
    assert len(dialled) == 1
    req = dialled[0]
    assert req.url.host == "93.184.216.34", "connection must be pinned to the validated IP"
    assert req.headers.get("Host") == "hooks.example.com", "Host header preserved"
    assert req.extensions.get("sni_hostname") == "hooks.example.com", "TLS SNI preserved"


# ════════════════════════════════════════════════════════════════════════════════════
# (b) which HTTP outcomes retry: 5xx / 429 / timeout / conn-refused YES; 4xx NO
# ════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("code", [500, 502, 503, 429])
async def test_retryable_status_enqueues(code, fake_redis):
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=code)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "queued" and res.queued is True
    assert await queue.depth() == 1, f"HTTP {code} must enqueue for retry"


@pytest.mark.parametrize("exc", [TimeoutErr("timed out"), ConnErr("refused")])
async def test_transport_error_enqueues(exc, fake_redis):
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(script=[exc])
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "queued" and res.queued is True
    assert await queue.depth() == 1, "a timeout / connection error must enqueue for retry"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 422])
async def test_4xx_does_not_retry(code, fake_redis):
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=code)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "failed", f"HTTP {code} is a permanent client error, must NOT retry"
    assert res.status_code == code
    assert await queue.depth() == 0, f"HTTP {code} must not be enqueued"


async def test_3xx_treated_as_failure_not_retried(fake_redis):
    """A 3xx (redirect) is < 300? No — 300 is NOT <300. It's not 5xx/429 either, so it's a
    permanent 'failed' (no retry). Documents the exact boundary at code==300."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=302)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "failed" and res.status_code == 302
    assert await queue.depth() == 0


async def test_299_is_delivered_boundary(fake_redis):
    """code < 300 == delivered; 299 is the last delivered code."""
    t = ScriptedTransport(default_code=299)
    sink = WebhookSink(transport=t, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "delivered" and res.status_code == 299


# ════════════════════════════════════════════════════════════════════════════════════
# (a) backoff delays follow the documented exponential schedule + cap
# ════════════════════════════════════════════════════════════════════════════════════


def test_documented_schedule_constants():
    # Pin the exact schedule so a silent edit to the constant trips this test.
    assert BACKOFF_SCHEDULE == [60, 300, 1800, 7200], "1m, 5m, 30m, 2h"
    assert MAX_AGE_SECONDS == 86400  # 24h cap on age
    # No jitter: the schedule is deterministic integers (no randomization).
    assert all(isinstance(d, int) for d in BACKOFF_SCHEDULE)


async def test_enqueue_first_retry_uses_first_backoff(fake_redis):
    queue = RetryQueue(fake_redis)
    await queue.enqueue(url=URL, envelope={"x": 1}, webhook_secret=SECRET, now=1000.0)
    raw = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
    entry = json.loads(raw)
    assert entry["attempt"] == 0
    assert entry["next_retry_at"] == 1000.0 + BACKOFF_SCHEDULE[0], "first retry after 60s"
    assert entry["created_at"] == 1000.0


async def test_backoff_delays_match_schedule_each_sweep(fake_redis):
    """Drive the clock forward sweep-by-sweep and assert the EFFECTIVE wait sequence a target
    experiences is exactly the documented schedule [60, 300, 1800, 7200] (WH3 fixed)."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)  # always fail → keep re-queuing
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)

    base = 10_000_000_000.0
    # Initial sync delivery fails → enqueued with attempt=0, next_retry_at=base+60.
    await sink.deliver(URL, build_envelope("meeting.completed", {"m": 1}), SECRET,
                       events_config=SUBSCRIBED, now=base) if False else None
    # WebhookSink.enqueue() uses time.time() for `now`; enqueue directly for a deterministic clock.
    await queue.enqueue(url=URL, envelope=build_envelope("meeting.completed", {"m": 1}),
                        webhook_secret=SECRET, now=base)

    # The first wait is the one enqueue set (base+60); each later wait is the one a failed drain
    # sweep sets on the requeued entry. Together they form the effective wait sequence.
    first_due = json.loads(await fake_redis.lindex(RETRY_QUEUE_KEY, 0))["next_retry_at"]
    effective_delays: List[float] = [first_due - base]
    clock = base
    for _ in range(len(BACKOFF_SCHEDULE) + 2):
        depth = await queue.depth()
        if depth == 0:
            break  # exhausted/dropped
        # Advance just past the due time so the single entry is processed this sweep.
        raw = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
        due = json.loads(raw)["next_retry_at"]
        clock = due + 1
        await drain_retry_queue(fake_redis, t, now=clock)
        if await queue.depth() == 0:
            break
        nxt = json.loads(await fake_redis.lindex(RETRY_QUEUE_KEY, 0))
        effective_delays.append(nxt["next_retry_at"] - clock)

    # WH3 FIXED: enqueue sets the first wait to BACKOFF[0] (60) and the drain indexes the NEXT
    # wait by attempt+1, so the effective sequence is exactly [60, 300, 1800, 7200] — the 1m
    # backoff no longer fires twice. The delays SET during drain are BACKOFF_SCHEDULE[1:].
    assert effective_delays == [float(d) for d in BACKOFF_SCHEDULE], (
        f"effective wait sequence diverged from the documented schedule: {effective_delays} "
        f"!= {[float(d) for d in BACKOFF_SCHEDULE]}"
    )


async def test_backoff_caps_at_last_entry(fake_redis):
    """backoff_idx = min(attempt, len-1) — once attempt >= last index the delay is pinned at
    the cap (7200), never growing further. Proven via the requeued delay on the last live sweep."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)
    base = 20_000_000_000.0
    await queue.enqueue(url=URL, envelope={"m": 1}, webhook_secret=SECRET, now=base)

    clock = base
    last_requeue_delay = None
    for _ in range(len(BACKOFF_SCHEDULE) + 2):
        if await queue.depth() == 0:
            break
        raw = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
        due = json.loads(raw)["next_retry_at"]
        clock = due + 1
        await drain_retry_queue(fake_redis, t, now=clock)
        if await queue.depth() > 0:
            nxt = json.loads(await fake_redis.lindex(RETRY_QUEUE_KEY, 0))
            last_requeue_delay = nxt["next_retry_at"] - clock
    # The largest delay ever applied is the cap, never exceeded.
    assert last_requeue_delay is None or last_requeue_delay <= float(BACKOFF_SCHEDULE[-1])


# ════════════════════════════════════════════════════════════════════════════════════
# (c) after max attempts the envelope is dropped (no infinite loop)
# ════════════════════════════════════════════════════════════════════════════════════


async def test_exhausted_schedule_drops_entry(fake_redis):
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)  # 500s forever
    base = 30_000_000_000.0
    await queue.enqueue(url=URL, envelope=build_envelope("meeting.completed", {"m": 1}),
                        webhook_secret=SECRET, now=base)

    clock = base
    sweeps = 0
    for _ in range(len(BACKOFF_SCHEDULE) + 5):
        if await queue.depth() == 0:
            break
        raw = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
        due = json.loads(raw)["next_retry_at"]
        clock = due + 1
        await drain_retry_queue(fake_redis, t, now=clock)
        sweeps += 1
    assert await queue.depth() == 0, "entry must be dropped after the schedule is exhausted"
    # WH3 FIXED: the entry is DELIVERED on attempts 0,1,2,3 (re-queued each time); on the
    # attempt==3 sweep _deliver_one runs, fails, and next_idx==4 exhausts the schedule → the
    # entry is dead-lettered instead of re-queued. So it survives exactly len(schedule) due sweeps.
    assert sweeps == len(BACKOFF_SCHEDULE), f"unexpected sweep count {sweeps}"


async def test_total_delivery_attempts_is_bounded(fake_redis):
    """End-to-end: 1 initial sync attempt + 4 drain attempts = exactly 5 HTTP POSTs, then drop.

    WH3 FIXED: the queued entry is delivered on drain attempts 0,1,2,3; the attempt==3 sweep
    fails and exhausts the schedule (dead-lettered, no extra delivery). The bound is the intended
    1 + len(schedule) = 5 and proves there is NO infinite retry loop."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    base = 40_000_000_000.0

    # Initial synchronous attempt (counts as call #1) → enqueues. Uses real time.time() for
    # next_retry_at, so fix up the entry's clock to our deterministic base for the sweeps.
    await sink.deliver(URL, build_envelope("meeting.completed", {"m": 1}), SECRET,
                       events_config=SUBSCRIBED)
    raw = json.loads(await fake_redis.lindex(RETRY_QUEUE_KEY, 0))
    raw["created_at"] = base
    raw["next_retry_at"] = base + BACKOFF_SCHEDULE[0]
    await fake_redis.lset(RETRY_QUEUE_KEY, 0, json.dumps(raw))

    clock = base
    for _ in range(len(BACKOFF_SCHEDULE) + 3):
        if await queue.depth() == 0:
            break
        cur = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
        if cur is None:
            break
        clock = json.loads(cur)["next_retry_at"] + 1
        await drain_retry_queue(fake_redis, t, now=clock)

    assert await queue.depth() == 0
    # 1 sync + 4 drain (attempts 0..3; the attempt==3 sweep delivers, fails, then dead-letters) = 5.
    expected_total = 1 + len(BACKOFF_SCHEDULE)
    assert t.calls == expected_total, (
        f"expected 1 sync + {len(BACKOFF_SCHEDULE)} drain attempts = "
        f"{expected_total} total HTTP POSTs (bounded, no infinite loop); got {t.calls}"
    )


async def test_expired_entry_dropped_before_delivery(fake_redis):
    """An entry older than MAX_AGE_SECONDS is dropped on the next sweep without a delivery."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)
    base = 50_000_000_000.0
    await queue.enqueue(url=URL, envelope={"m": 1}, webhook_secret=SECRET, now=base)
    # Sweep far past MAX_AGE → expired branch fires, no delivery, entry gone.
    await drain_retry_queue(fake_redis, t, now=base + MAX_AGE_SECONDS + 1)
    assert await queue.depth() == 0
    assert t.calls == 0, "an expired entry must be dropped without attempting delivery"


async def test_dead_letter_on_permanent_failure(fake_redis, capsys):
    """WH1 FIXED: an envelope that exhausts the schedule lands in the dead-letter queue
    (with routing + last-failure metadata) and emits a `webhook_dead_lettered` log event."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)
    base = 60_000_000_000.0
    await queue.enqueue(url=URL, envelope=build_envelope("meeting.completed", {"m": 99}),
                        webhook_secret=SECRET, now=base, label="meeting:99")
    clock = base
    for _ in range(len(BACKOFF_SCHEDULE) + 2):
        if await queue.depth() == 0:
            break
        cur = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
        clock = json.loads(cur)["next_retry_at"] + 1
        await drain_retry_queue(fake_redis, t, now=clock)

    # The permanently-failed envelope lands in the dead-letter queue (not silently dropped).
    assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0, "retry queue must be drained"
    dlq_depth = await fake_redis.llen(DEAD_LETTER_KEY)
    assert dlq_depth == 1, (
        "expected the permanently-failed envelope to land in a dead-letter queue "
        f"('{DEAD_LETTER_KEY}'); found {dlq_depth}"
    )
    record = json.loads(await fake_redis.lindex(DEAD_LETTER_KEY, 0))
    assert record["url"] == URL
    assert record["label"] == "meeting:99"
    assert record["reason"] == "schedule_exhausted"
    assert record["last_status_code"] == 500
    assert record["payload"]["data"] == {"m": 99}  # the original envelope is preserved
    assert record["created_at"] == base

    # An operator-visible log_event was emitted (audience=system, level=warning).
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip().startswith("{")]
    dl_logs = [e for e in lines if e.get("event") == "webhook_dead_lettered"]
    assert len(dl_logs) == 1, "expected exactly one webhook_dead_lettered log event"
    log = dl_logs[0]
    assert log["audience"] == "system" and log["level"] == "warning"
    assert log["fields"]["url"] == URL
    assert log["fields"]["reason"] == "schedule_exhausted"
    assert log["fields"]["last_status_code"] == 500


async def test_dead_letter_on_age_expiry(fake_redis, capsys):
    """WH1 FIXED: an age-expired entry is dead-lettered (not silently dropped) without a delivery."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=500)
    base = 61_000_000_000.0
    await queue.enqueue(url=URL, envelope=build_envelope("meeting.completed", {"m": 7}),
                        webhook_secret=SECRET, now=base, label="meeting:7")
    await drain_retry_queue(fake_redis, t, now=base + MAX_AGE_SECONDS + 1)
    assert await queue.depth() == 0
    assert t.calls == 0, "an expired entry must be dead-lettered without attempting delivery"
    assert await fake_redis.llen(DEAD_LETTER_KEY) == 1
    record = json.loads(await fake_redis.lindex(DEAD_LETTER_KEY, 0))
    assert record["reason"] == "max_age_exceeded" and record["label"] == "meeting:7"
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip().startswith("{")]
    assert any(
        e.get("event") == "webhook_dead_lettered" and e["fields"]["reason"] == "max_age_exceeded"
        for e in lines
    ), "expected a webhook_dead_lettered log for the age-expired entry"


# ════════════════════════════════════════════════════════════════════════════════════
# (g) a successful delivery is never re-delivered
# ════════════════════════════════════════════════════════════════════════════════════


async def test_2xx_never_enqueued(fake_redis):
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(default_code=200)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"m": 1})
    res = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert res.status == "delivered"
    assert await queue.depth() == 0, "a 2xx must never be enqueued for retry"
    # A subsequent drain has nothing to do → no re-delivery.
    processed = await drain_retry_queue(fake_redis, t, now=99_999_999_999.0)
    assert processed == 0
    assert t.calls == 1, "successful delivery must not be re-sent"


async def test_recover_after_n_failures_delivers_once_then_stops(fake_redis):
    """500, 500, then 200: the queue drains to empty after the recovery and is not re-delivered."""
    queue = RetryQueue(fake_redis)
    # Initial sync attempt fails (500); two drain attempts: 500 then 200.
    t = ScriptedTransport(script=[500, 500, 200], default_code=200)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    base = 70_000_000_000.0
    res = await sink.deliver(URL, build_envelope("meeting.completed", {"m": 1}), SECRET,
                             events_config=SUBSCRIBED)
    assert res.status == "queued"

    # Normalize the entry's clock to base for deterministic sweeps.
    raw = json.loads(await fake_redis.lindex(RETRY_QUEUE_KEY, 0))
    raw["created_at"] = base
    raw["next_retry_at"] = base + BACKOFF_SCHEDULE[0]
    await fake_redis.lset(RETRY_QUEUE_KEY, 0, json.dumps(raw))

    clock = base
    for _ in range(len(BACKOFF_SCHEDULE) + 2):
        if await queue.depth() == 0:
            break
        cur = await fake_redis.lindex(RETRY_QUEUE_KEY, 0)
        clock = json.loads(cur)["next_retry_at"] + 1
        await drain_retry_queue(fake_redis, t, now=clock)

    assert await queue.depth() == 0, "queue must be empty after a successful recovery"
    # 1 sync (500) + drain 500 + drain 200 = 3 HTTP POSTs; the 200 is final, no re-send.
    assert t.calls == 3, f"expected 3 attempts (fail, fail, succeed); got {t.calls}"
    # Extra drain after success is a no-op.
    before = t.calls
    await drain_retry_queue(fake_redis, t, now=clock + 1_000_000)
    assert t.calls == before, "a delivered entry must not be re-delivered"


async def test_at_least_once_redelivered_body_still_verifies(fake_redis):
    """The redelivered body (headers rebuilt with a fresh ts on the drain) still verifies."""
    queue = RetryQueue(fake_redis)
    t = ScriptedTransport(script=[500], default_code=200)
    sink = WebhookSink(transport=t, queue=queue, resolver=_PUBLIC)
    base = 80_000_000_000.0
    await sink.deliver(URL, build_envelope("meeting.completed", {"m": 1}), SECRET,
                       events_config=SUBSCRIBED)
    raw = json.loads(await fake_redis.lindex(RETRY_QUEUE_KEY, 0))
    raw["next_retry_at"] = base
    await fake_redis.lset(RETRY_QUEUE_KEY, 0, json.dumps(raw))
    await drain_retry_queue(fake_redis, t, now=base + 1)
    assert await queue.depth() == 0
    redelivered = t.received[-1]
    assert _independent_verify(redelivered["body"], redelivered["headers"], SECRET), (
        "redelivered envelope must carry a valid HMAC over its fresh ts"
    )
