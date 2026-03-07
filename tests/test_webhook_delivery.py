"""Tests for webhook delivery with retry and signing."""
import hashlib
import hmac
import json
import pytest


def test_sign_payload():
    from shared_models.webhook_delivery import sign_payload
    payload = b'{"event": "test"}'
    secret = "test-secret-123"
    sig = sign_payload(payload, secret)
    assert sig.startswith("sha256=")
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


def test_build_headers_no_secret():
    from shared_models.webhook_delivery import build_headers
    headers = build_headers()
    assert headers == {"Content-Type": "application/json"}


def test_build_headers_with_secret():
    from shared_models.webhook_delivery import build_headers
    payload = b'{"event": "test"}'
    headers = build_headers(webhook_secret="my-secret", payload_bytes=payload)
    assert headers["Authorization"] == "Bearer my-secret"
    assert "X-Webhook-Signature" in headers
    assert headers["X-Webhook-Signature"].startswith("sha256=")
    assert "X-Webhook-Timestamp" in headers


def test_build_headers_empty_secret():
    from shared_models.webhook_delivery import build_headers
    headers = build_headers(webhook_secret="  ", payload_bytes=b"test")
    # Empty/whitespace secret should not add auth headers
    assert "Authorization" not in headers


def test_signature_is_verifiable():
    """Client should be able to verify the signature."""
    from shared_models.webhook_delivery import build_headers
    secret = "webhook-secret-abc"
    payload = json.dumps({"event": "meeting.completed", "id": 123}).encode()
    headers = build_headers(webhook_secret=secret, payload_bytes=payload)

    # Simulate client-side verification
    ts = headers["X-Webhook-Timestamp"]
    received_sig = headers["X-Webhook-Signature"].replace("sha256=", "")
    signed_content = f"{ts}.".encode() + payload
    expected_sig = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
    assert received_sig == expected_sig


def test_retry_module_imports():
    from shared_models.retry import with_retry, _is_retryable
    assert callable(with_retry)
    assert callable(_is_retryable)


@pytest.mark.asyncio
async def test_retry_succeeds_first_try():
    from shared_models.retry import with_retry

    call_count = 0

    async def ok_fn():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await with_retry(ok_fn, label="test")
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_retries_on_transient():
    import httpx
    from shared_models.retry import with_retry

    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("connection refused")
        return "recovered"

    result = await with_retry(flaky, base_delay=0.01, label="test")
    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_no_retry_on_client_error():
    import httpx
    from shared_models.retry import with_retry

    call_count = 0

    async def client_error():
        nonlocal call_count
        call_count += 1
        request = httpx.Request("POST", "http://example.com")
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(client_error, max_retries=3, base_delay=0.01, label="test")
    assert call_count == 1
