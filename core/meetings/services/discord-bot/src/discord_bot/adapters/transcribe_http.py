"""Transcription client — OpenAI-compatible multipart POST to the invocation's
``transcriptionServiceUrl`` (Vexa's own Whisper worker).

Ported from the bridge's ``bot.py`` ``transcribe()``: same wire shape (multipart ``file`` + ``model``
+ optional ``language``), now sourced from the invocation instead of a hardcoded env var — every
meeting-bot kind resolves its STT endpoint this way (#511).

Retries only on a connection error (the worker restarting / still loading its model); a timeout
means the worker is overloaded and is NOT retried (retrying would only deepen the backlog).
Returns ``None`` when the worker is unavailable (the caller should retry later) and ``""`` when the
worker responded 200 OK with no speech detected (a legitimate empty result, not a failure).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, Protocol

#: (url, *, data, files, headers, timeout) -> response-like with .status_code / .json(). The real
#: adapter wraps httpx.AsyncClient.post.
class PostFn(Protocol):
    async def __call__(
        self, url: str, *, data: dict[str, str], files: dict[str, Any], timeout: float
    ) -> Any: ...


async def transcribe(
    wav: bytes,
    *,
    url: str,
    post: PostFn,
    token: Optional[str] = None,
    language: Optional[str] = None,
    model: str = "whisper-1",
    timeout: float = 600.0,
    attempts: int = 3,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    sleep = sleep or asyncio.sleep
    log = log or (lambda m: print(m, flush=True))
    data = {"model": model}
    if language:
        data["language"] = language
    files = {"file": ("utterance.wav", wav, "audio/wav")}
    for attempt in range(attempts):
        try:
            res = await post(url, data=data, files=files, timeout=timeout)
        except ConnectionError as e:
            if attempt < attempts - 1:
                await sleep(2 * (attempt + 1))  # 2s, 4s backoff
                continue
            log(f"[discord-bot] transcribe worker unreachable after {attempts} tries: {e}")
            return None
        except Exception as e:  # noqa: BLE001 — any other failure: log + give up (don't retry blind)
            log(f"[discord-bot] transcribe error: {e}")
            return None
        if res.status_code != 200:
            log(f"[discord-bot] transcribe HTTP {res.status_code}")
            return None
        body = res.json()
        return (body.get("text") or "").strip()
    return None
