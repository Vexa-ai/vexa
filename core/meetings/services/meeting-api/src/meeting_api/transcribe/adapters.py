"""Production adapters for the transcribe module (#525 C1).

``HttpSttTranscriber`` speaks the OpenAI-compatible ``POST /v1/audio/transcriptions``
contract — the single ASR seam (#437 ruling) — against the deployment's configured
backend (bundled unit or a remote provider like Groq). ``master_audio_resolver``
composes the recordings seam (finalize-on-read, #768) into the byte-resolver port
the service flow consumes.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx

from .service import TranscribeFault

_ENDPOINT_PATH = "/v1/audio/transcriptions"


def _provider_fault(resp: httpx.Response) -> TranscribeFault:
    """An HTTP error response → a typed fault carrying the provider's own words.

    Understands both the Groq/OpenAI error envelope ({"error": {message, code}})
    and the bundled service's FastAPI shape ({"detail": ...}). The message travels
    with the fault: an operator must see WHY, not 0.10's generic
    "Transcription service error: 404" (#355 defect 1).
    """
    code: Optional[str] = None
    try:
        body = resp.json()
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            message = err.get("message") or str(err)
        else:
            message = str(body.get("detail") or resp.text)
    except Exception:  # noqa: BLE001 — a non-JSON error body still surfaces verbatim
        message = resp.text
    return TranscribeFault(
        "provider_rejected", message, status=resp.status_code, provider_code=code,
    )


class HttpSttTranscriber:
    """The deferred-tier STT client over httpx.

    Sends the resolved model id + ``response_format=verbose_json`` +
    ``transcription_tier=deferred`` (#355 defects 1+2, the deferred capacity tier),
    and loops on 503 honoring Retry-After — the bundled service fails fast when
    busy, so a deferred caller waits its turn.
    """

    def __init__(
        self,
        base_url: Optional[str],
        *,
        token: Optional[str] = None,
        model: str = "whisper-1",
        transport: Optional[httpx.AsyncBaseTransport] = None,
        max_busy_retries: int = 60,
    ):
        self.base_url = base_url
        self.token = token
        self.model = model
        self._transport = transport
        self._max_busy_retries = max_busy_retries

    @classmethod
    def from_env(cls) -> "HttpSttTranscriber":
        """The deployment's configured backend: #522's TRANSCRIPTION_MODEL (default
        whisper-1) against TRANSCRIPTION_SERVICE_URL/TOKEN."""
        return cls(
            os.getenv("TRANSCRIPTION_SERVICE_URL") or None,
            token=os.getenv("TRANSCRIPTION_SERVICE_TOKEN") or None,
            model=os.getenv("TRANSCRIPTION_MODEL") or "whisper-1",
        )

    def _endpoint(self) -> str:
        # Append-only-when-missing — the ONE url rule the TS client and
        # config_preflight already share; anything else double-paths into a 404.
        base = (self.base_url or "").rstrip("/")
        return base if base.endswith(_ENDPOINT_PATH) else base + _ENDPOINT_PATH

    async def transcribe(self, audio: bytes, *, language: Optional[str] = None) -> dict:
        if not self.base_url:
            raise TranscribeFault(
                "stt_unconfigured", "TRANSCRIPTION_SERVICE_URL is not set on this deployment",
            )
        data = {
            "model": self.model,
            "response_format": "verbose_json",
            "transcription_tier": "deferred",
        }
        if language:
            data["language"] = language
        # ponytail: filename hint is master.wav regardless of container; thread the real
        # fmt through the resolver if a provider ever starts sniffing extensions.
        files = {"file": ("master.wav", audio, "audio/wav")}
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=httpx.Timeout(600.0, connect=10.0),
            ) as client:
                for attempt in range(self._max_busy_retries + 1):
                    resp = await client.post(
                        self._endpoint(), data=data, files=files, headers=headers,
                    )
                    if resp.status_code != 503 or attempt == self._max_busy_retries:
                        break
                    try:
                        retry_after = float(resp.headers.get("Retry-After", "1"))
                    except ValueError:
                        retry_after = 1.0
                    await asyncio.sleep(retry_after)
        except httpx.HTTPError as e:
            raise TranscribeFault("unavailable", f"transcription backend unreachable: {e}")

        if resp.status_code == 503:
            raise TranscribeFault(
                "unavailable", "transcription backend busy — retries exhausted", status=503,
            )
        if resp.status_code >= 400:
            raise _provider_fault(resp)
        try:
            return resp.json()
        except ValueError:
            raise TranscribeFault(
                "provider_rejected", "transcription backend returned a non-JSON body",
                status=resp.status_code,
            )


def master_audio_resolver(repo, storage):
    """recordings seam → the service's byte-resolver port.

    Finalize-on-read over every recording of the meeting (a meeting can carry
    several); the first media file that finalizes to a master wins. None when
    nothing finalizes — recording disabled, or nothing captured.
    """
    from ..recordings.service import finalize_master

    async def resolve(meeting_id: int) -> Optional[bytes]:
        for rec in await repo.get_recordings(meeting_id):
            key = await finalize_master(
                repo, storage, meeting_id=meeting_id, recording_id=rec.get("id"),
                media_type="audio",
            )
            if key is not None:
                return await storage.get(key)
        return None

    return resolve
