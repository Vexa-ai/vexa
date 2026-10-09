"""Exercise the canonical probe over real localhost HTTP connections."""
import asyncio

import pytest

from admin_api.app.transcription_probe import (
    OK, UNAUTHORIZED, UNREACHABLE, probe_transcription_endpoint,
)
from stt_stub import closed_port_url, parse_multipart, stt_stub, wav_facts


def test_ok_and_request_shape():
    with stt_stub() as (url, requests):
        result = asyncio.run(probe_transcription_endpoint(url, "probe-secret"))
    assert result.status == OK and result.ok and result.detail is None
    assert len(requests) == 1
    request = requests[0]
    assert request["path"] == "/v1/audio/transcriptions"
    assert request["headers"]["authorization"] == "Bearer probe-secret"
    parts = parse_multipart(request["headers"]["content-type"], request["body"])
    assert parts["model"] == "whisper-1"
    assert parts["response_format"] == "json"
    assert set(parts) == {"file", "model", "response_format"}
    audio = parts["file"]
    assert audio["filename"] == "probe.wav"
    assert audio["content_type"] == "audio/wav"
    assert wav_facts(audio["body"]) == {
        "riff": b"RIFF", "wave": b"WAVE", "audio_format": 1,
        "channels": 1, "sample_rate": 16000, "bits_per_sample": 16,
        "data_bytes": 16000 * 2,
    }
    assert any(audio["body"][-32000:])  # The canonical probe is a tone, not silence.


def test_no_double_path():
    with stt_stub() as (url, requests):
        result = asyncio.run(probe_transcription_endpoint(url + "/v1/audio/transcriptions"))
    assert result.ok
    assert requests[0]["path"] == "/v1/audio/transcriptions"


@pytest.mark.parametrize("status", [401, 403])
def test_unauthorized(status):
    with stt_stub(status=status) as (url, _requests):
        result = asyncio.run(probe_transcription_endpoint(url))
    assert result.status == UNAUTHORIZED and not result.ok
    assert url in result.detail and str(status) in result.detail


def test_connection_refused():
    url = closed_port_url()
    result = asyncio.run(probe_transcription_endpoint(url))
    assert result.status == UNREACHABLE
    assert url in result.detail and "ConnectError" in result.detail


def test_timeout():
    with stt_stub(delay=1.0) as (url, _requests):
        result = asyncio.run(probe_transcription_endpoint(url, timeout=0.2))
    assert result.status == UNREACHABLE
    assert url in result.detail and "timeout" in result.detail and "0.2" in result.detail


@pytest.mark.parametrize("status,body", [
    (404, b'not found'), (200, b'{"ok":true}'), (200, b'not JSON'),
    (200, b'[]'), (200, b'null'), (200, b'{"text":null}'), (200, b'{"text":42}'),
    (302, b'redirect'), (500, b'backend failed'),
])
def test_wrong_shape_or_status(status, body):
    with stt_stub(status=status, body=body) as (url, _requests):
        result = asyncio.run(probe_transcription_endpoint(url))
    assert result.status == UNREACHABLE
    assert url in result.detail
    if status != 200:
        assert str(status) in result.detail and body.decode() in result.detail


def test_token_never_appears_in_detail():
    token = "distinctive-secret-8294"
    for status in (401, 404):
        with stt_stub(status=status, body=f"rejected {token}".encode()) as (url, _requests):
            result = asyncio.run(probe_transcription_endpoint(url + "/" + token, token))
        assert not result.ok and result.detail
        assert token not in result.detail
    result = asyncio.run(probe_transcription_endpoint(closed_port_url(), token))
    assert result.status == UNREACHABLE and result.detail
    assert token not in result.detail


def test_no_token_no_authorization_header():
    with stt_stub() as (url, requests):
        result = asyncio.run(probe_transcription_endpoint(url, token=None))
    assert result.ok
    assert "authorization" not in requests[0]["headers"]


def test_body_excerpt_truncated():
    with stt_stub(status=404, body=b"x" * 200 + b"omitted") as (url, _requests):
        result = asyncio.run(probe_transcription_endpoint(url))
    assert result.status == UNREACHABLE
    assert result.detail.endswith("x" * 200)
    assert "omitted" not in result.detail


def test_empty_text_is_valid():
    with stt_stub(body=b'{"text":""}') as (url, _requests):
        result = asyncio.run(probe_transcription_endpoint(url))
    assert result.ok and result.detail is None
