"""Streaming PCM16 adapter for the transcription brick's verbose_json contract.
STT_BACKEND selects whisper (default) or transcribe in the service entrypoint.
AWS_REGION is required for streaming; there is no implicit region.
TRANSCRIBE_LANGUAGE_DEFAULT supplies the language when no known mapping exists.
TRANSCRIBE_TIMEOUT_S bounds each stream below the bot's request timeout.
Credentials use the standard AWS chain: environment, profile, or IAM.
Credential resolution failures become HTTP 503 with Retry-After: 5.
Throttling, service/internal failures and timeouts become 503 / Retry-After: 1.
BadRequestException and undecodable WAV uploads become HTTP 422.
Only final results become segments; partial hypotheses are discarded.
Segment times are emitted as received; duration is the maximum segment end.
The endpoint preserves the five response keys and twelve base segment keys.
Word metadata is collected here and exposed only when requested by the caller.
No speaker labels are requested: identity comes from per-participant audio.
The amazon-transcribe streaming package is licensed under Apache-2.0.
"""

import asyncio
import os
from threading import Lock

from amazon_transcribe import AWSCRTEventLoop
from amazon_transcribe.auth import AwsCredentialsProvider, CredentialResolver
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.model import TranscriptEvent

DEFAULT_LANGUAGE = "en-US"
DEFAULT_TIMEOUT_S = 25.0
LANGUAGES = {
    "en": "en-US", "de": "de-DE", "fr": "fr-FR", "es": "es-ES",
    "it": "it-IT", "pt": "pt-PT", "nl": "nl-NL", "ja": "ja-JP",
    "ko": "ko-KR", "zh": "zh-CN",
}
_credential_provider = None
_credential_provider_lock = Lock()


def _get_credential_provider():
    """Share the CRT chain and its refresh cache across requests and health probes."""
    global _credential_provider
    with _credential_provider_lock:
        if _credential_provider is None:
            _credential_provider = AwsCredentialsProvider.new_default_chain(
                AWSCRTEventLoop().bootstrap
            )
        return _credential_provider


def map_language(iso639_1_or_none: str | None, default: str) -> str:
    if not iso639_1_or_none:
        return default
    if "-" in iso639_1_or_none:
        return iso639_1_or_none
    return LANGUAGES.get(iso639_1_or_none.lower(), default)


def configuration() -> tuple[str, str, float]:
    """Read backend configuration; reject deadlines at or beyond the client abort."""
    region = os.getenv("AWS_REGION", "").strip()
    if not region:
        raise ValueError("AWS_REGION is required for transcribe")
    language = os.getenv("TRANSCRIBE_LANGUAGE_DEFAULT") or DEFAULT_LANGUAGE
    timeout = float(os.getenv("TRANSCRIBE_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
    if not 0 < timeout < 30:
        raise ValueError("TRANSCRIBE_TIMEOUT_S must be positive and below 30 seconds")
    return region, language, timeout


class CredentialsUnavailable(Exception):
    """The SDK's default credential chain could not produce signing credentials."""


async def resolve_credentials():
    # CRT raises AwsCrtError (AWS_AUTH_CREDENTIALS_PROVIDER_*) rather than the
    # SDK's CredentialsException. Translate only at the resolver boundary so
    # unrelated transport errors are not misclassified as credential failures.
    try:
        provider = _get_credential_provider()
        credentials = await asyncio.wait_for(
            asyncio.wrap_future(provider.get_credentials()), timeout=5
        )
        if credentials is None:
            raise ValueError("AWS credential chain returned no credentials")
        return credentials
    except Exception as exc:
        reason = f"credentials unresolvable: {type(exc).__name__}: {exc}"[:200]
        raise CredentialsUnavailable(reason) from exc


class _CheckedCredentialResolver(CredentialResolver):
    def __init__(self):
        self._credentials = None
        self._lock = asyncio.Lock()

    async def get_credentials(self):
        # A client serves one bounded window. Reuse its initial signing credentials
        # for each audio event; the shared provider handles refresh between windows.
        async with self._lock:
            if self._credentials is None:
                self._credentials = await resolve_credentials()
            return self._credentials


def create_client(*, region: str):
    return TranscribeStreamingClient(
        region=region, credential_resolver=_CheckedCredentialResolver()
    )


async def transcribe_pcm16(
    pcm: bytes, sample_rate: int, language_code: str, *, region: str,
    timeout_s: float, client_factory=None,
) -> tuple[list[dict], float]:
    """Send a window while consuming results; cancel both tasks on any failure."""
    async def run():
        client = (client_factory or create_client)(region=region)
        stream = await client.start_stream_transcription(
            language_code=language_code, media_sample_rate_hz=sample_rate,
            media_encoding="pcm",
        )
        segments = []

        async def send():
            try:
                # 100ms PCM chunks, never exceeding the SDK's 32KB event limit.
                chunk_size = min(max(2, sample_rate // 10 * 2), 32768)
                for offset in range(0, len(pcm), chunk_size):
                    await stream.input_stream.send_audio_event(
                        audio_chunk=pcm[offset:offset + chunk_size]
                    )
            finally:
                await stream.input_stream.end_stream()

        async def receive():
            async for event in stream.output_stream:
                if not isinstance(event, TranscriptEvent):
                    continue
                for result in event.transcript.results:
                    if result.is_partial or not result.alternatives:
                        continue
                    alternative = result.alternatives[0]
                    segments.append({
                        "id": len(segments), "seek": 0,
                        "start": result.start_time, "end": result.end_time,
                        "text": alternative.transcript, "tokens": [],
                        "temperature": 0.0, "avg_logprob": 0.0,
                        "compression_ratio": 1.0, "no_speech_prob": 0.0,
                        "audio_start": result.start_time, "audio_end": result.end_time,
                        "words": [
                            {"word": item.content, "start": item.start_time,
                             "end": item.end_time, "probability": 1.0}
                            for item in (alternative.items or [])
                            if item.item_type == "pronunciation"
                        ],
                    })

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return segments, max((segment["end"] for segment in segments), default=0.0)

    return await asyncio.wait_for(run(), timeout=timeout_s)
