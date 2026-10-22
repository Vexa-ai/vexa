"""The languages a caller's bots can transcribe — ``GET /transcription/languages``
(transcription-language.v1 ``LanguageCatalog``).

The list belongs to the transcription MODEL, so the answer is found in three steps:

  1. THE BACKEND — the one this caller's bots transcribe with, chosen exactly as a spawn chooses it:
     their Settings backend from identity's bot-context answer, else the deployment's
     ``TRANSCRIPTION_SERVICE_URL``. The lookup is best effort; identity unreachable means the
     deployment's backend.
  2. THE MODEL — a backend this deployment owns (the env one, or a Settings one identity marks
     ``provider: vexa``) is asked: ``GET {url}/health`` → ``model``, as the bundled transcription unit
     answers. A customer's own endpoint is never called from here; its configured model id is used.
     The backend's answer, or its silence, is cached per URL for ``PROBE_TTL_S``.
  3. THE LIST — the sealed ``catalog.json`` beside the contract maps a model id to its codes. A model
     it does not list, or no model at all, gets every code the language fields accept (``assumed``).

``default`` is the deployment tier (``DEFAULT_TRANSCRIPTION_LANGUAGE`` /
``DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES``) as a setting, auto when unset. It is the lowest tier, not a
limit, so it never narrows ``languages``.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

from fastapi import APIRouter, Header

from . import transcription_language as tlang

_CATALOG_PATH = Path("meetings") / "contracts" / "transcription-language.v1" / "catalog.json"

#: How long a backend's answer (or its silence) about its model is kept.
PROBE_TTL_S = 300.0
#: How long the backend gets to answer.
PROBE_TIMEOUT_S = 2.0


def _load_catalog() -> dict:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _CATALOG_PATH
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"sealed contract not found by path: {_CATALOG_PATH}")


CATALOG = _load_catalog()
_LANGUAGES: list[dict] = [{"code": l["code"], "name": l["name"]} for l in CATALOG["languages"]]
_MODELS = [(re.compile(m["match"]), m) for m in CATALOG["models"]]


def languages_for(model: Optional[str]) -> Optional[list[dict]]:
    """``catalog.json``'s list for a model id, in catalog order, or None when no entry matches it.

    The id is lower-cased and cut after its last ``/`` (a Hugging Face path such as
    ``Systran/faster-whisper-large-v3``); the first entry whose ``match`` finds a match decides."""
    if not model:
        return None
    key = model.strip().lower().rsplit("/", 1)[-1]
    for pattern, entry in _MODELS:
        if pattern.search(key):
            if "only" in entry:
                return [l for l in _LANGUAGES if l["code"] in entry["only"]]
            return [l for l in _LANGUAGES if l["code"] not in entry["except"]]
    return None


def catalog(provider_model: Optional[str], configured_model: Optional[str],
            default: Optional[tlang.LanguageSetting]) -> dict:
    """The ``LanguageCatalog`` answer: the backend's own model first, then the configured one, then
    every code (``assumed``). A model the catalog does not list falls through to the next."""
    for model, source in ((provider_model, "provider"), (configured_model, "configured")):
        listed = languages_for(model)
        if listed is not None:
            return {"languages": listed, "default": (default or tlang.AUTO_SETTING).as_dict(),
                    "model": model, "source": source}
    return {"languages": list(_LANGUAGES), "default": (default or tlang.AUTO_SETTING).as_dict(),
            "model": provider_model or configured_model or None, "source": "assumed"}


class ModelProbe(Protocol):
    async def model_of(self, url: str, token: Optional[str]) -> Optional[str]:
        """The model the backend at ``url`` says it serves, or None when it does not say."""
        ...


class HealthProbe:
    """Asks ``GET {url}/health`` for ``model`` — the bundled transcription unit's answer, 200 when its
    model is loaded and 503 while it loads — and keeps the answer per URL for ``PROBE_TTL_S``."""

    def __init__(self, ttl_s: float = PROBE_TTL_S, timeout_s: float = PROBE_TIMEOUT_S,
                 clock: Callable[[], float] = time.monotonic, transport=None) -> None:
        self._ttl = ttl_s
        self._transport = transport
        self._timeout = timeout_s
        self._clock = clock
        self._seen: dict[str, tuple[float, Optional[str]]] = {}

    async def _ask(self, url: str, token: Optional[str]) -> Optional[str]:
        import httpx

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                r = await client.get(f"{url.rstrip('/')}/health", headers=headers)
            if r.status_code not in (200, 503):
                return None
            model = r.json().get("model")
        except Exception:  # noqa: BLE001 — a backend that cannot answer has named no model
            return None
        return model.strip() if isinstance(model, str) and model.strip() else None

    async def model_of(self, url: str, token: Optional[str]) -> Optional[str]:
        now = self._clock()
        seen = self._seen.get(url)
        if seen is not None and now - seen[0] < self._ttl:
            return seen[1]
        model = await self._ask(url, token)
        self._seen[url] = (now, model)
        return model


def _backend(bot_context: dict) -> tuple[Optional[str], Optional[str], Optional[str], bool]:
    """The backend a spawn for this caller would use: ``(url, token, configured model, owned)``.
    ``owned`` is whether this deployment operates it, and so may be asked about its model."""
    configured = bot_context.get("transcription")
    if isinstance(configured, dict) and configured.get("url"):
        return (configured["url"], configured.get("token") or None,
                configured.get("model") or None, configured.get("provider") == "vexa")
    return (os.getenv("TRANSCRIPTION_SERVICE_URL") or None,
            os.getenv("TRANSCRIPTION_SERVICE_TOKEN") or None,
            os.getenv("TRANSCRIPTION_MODEL") or None, True)


async def answer(user_id: int, bot_context: Callable[[int], Awaitable[dict]],
                 probe: ModelProbe) -> dict:
    url, token, configured_model, owned = _backend(await bot_context(user_id))
    provider_model = await probe.model_of(url, token) if url and owned else None
    return catalog(provider_model, configured_model, tlang.deployment_tier())


def build_language_catalog_router(bot_context: Callable[[int], Awaitable[dict]],
                                  probe: ModelProbe) -> APIRouter:
    from ..lifecycle.stop_router import _resolve_user_id

    router = APIRouter()

    @router.get("/transcription/languages")
    async def transcription_languages(x_user_id: Optional[str] = Header(default=None)):
        return await answer(_resolve_user_id(x_user_id), bot_context, probe)

    return router
