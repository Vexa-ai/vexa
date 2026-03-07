"""Exponential backoff retry for async HTTP operations."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 10.0

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRYABLE_STATUS_CODES:
        return True
    return False


async def with_retry(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY,
    label: str = "",
    **kwargs: Any,
) -> Any:
    """Call an async function with exponential backoff on transient failures."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < max_retries and _is_retryable(e):
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.5), MAX_DELAY)
                tag = f" [{label}]" if label else ""
                logger.warning(f"Retry{tag} attempt {attempt + 1}/{max_retries + 1}: {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # unreachable, but satisfies type checker
