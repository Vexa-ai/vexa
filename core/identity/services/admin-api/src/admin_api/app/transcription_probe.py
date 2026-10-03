"""Validate a customer's STT endpoint before persisting its configuration."""
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config_preflight import audio_probe_body, probe_url

PROBE_PATH = "/v1/audio/transcriptions"
PROBE_TIMEOUT_SECONDS = 10.0
PROBE_MODEL = "whisper-1"
OK = "ok"
UNAUTHORIZED = "unauthorized"
UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ProbeResult:
    status: str
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == OK


async def probe_transcription_endpoint(
    url: str, token: Optional[str] = None, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> ProbeResult:
    endpoint = probe_url(url, PROBE_PATH)
    content_type, body = audio_probe_body(PROBE_MODEL)
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def redact(value: str) -> str:
        return value.replace(token, "[redacted]") if token else value

    def failure(status: str, reason: str) -> ProbeResult:
        return ProbeResult(status, redact(f"{endpoint}: {reason}"))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, headers=headers, content=body)
    except httpx.TimeoutException:
        return failure(UNREACHABLE, f"timeout after {timeout:g} seconds")
    except httpx.HTTPError as exc:
        return failure(UNREACHABLE, type(exc).__name__)

    if response.status_code in (401, 403):
        return failure(UNAUTHORIZED, f"HTTP {response.status_code}")
    if not 200 <= response.status_code < 300:
        excerpt = redact(response.text)[:200]
        return failure(UNREACHABLE, f"HTTP {response.status_code}: {excerpt}")
    try:
        payload = response.json()
    except ValueError:
        return failure(UNREACHABLE, "response is not JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        return failure(UNREACHABLE, "response must be a JSON object with a string text field")
    return ProbeResult(OK)
