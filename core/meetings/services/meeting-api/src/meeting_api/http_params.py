"""Strict query-parameter handling for the HTTP surface.

A parameter the server does not implement is REFUSED, never accepted-and-dropped. Silently ignoring
an unknown query key answers 200 to a request whose caller believes it asked for something else —
`?limit=2` reads as "give me two segments" and hands back all of them, and the caller has no signal
that the slice never happened. The refusal IS the contract: a 400 that names the offending key and
lists what the endpoint accepts is the cheapest debugging tool the API can hand a caller.

Applied per-endpoint (an allow-list the route declares), not globally, so each surface owns the set
of inputs it honours.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from fastapi import HTTPException


def reject_unsupported_query_params(request, supported: Iterable[str]) -> None:
    """400 when the request carries a query key this endpoint does not honour.

    ``supported`` is the endpoint's whole accepted set; anything else is named back to the caller so
    a typo (`after_segment` for `since`) is one response away from being obvious."""
    allowed = set(supported)
    unknown = sorted({k for k in request.query_params.keys() if k not in allowed})
    if not unknown:
        return
    accepted = ", ".join(sorted(allowed)) if allowed else "(no query parameters)"
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unsupported query parameter(s): {', '.join(unknown)}. "
            f"This endpoint accepts: {accepted}."
        ),
    )


def parse_iso8601_utc(raw: str, *, param: str) -> datetime:
    """Parse an ISO-8601 instant into an aware UTC datetime, or 400 naming the parameter.

    A trailing ``Z`` is normalized to ``+00:00`` (``fromisoformat`` predates that spelling on the
    interpreters we ship); a zone-less value is read as UTC, which is what the API emits."""
    text = (raw or "").strip()
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid `{param}`: expected an ISO-8601 UTC timestamp "
                f"(e.g. 2026-08-18T08:20:00Z), got {raw!r}."
            ),
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_optional_iso8601_utc(raw: Optional[str], *, param: str) -> Optional[datetime]:
    """``parse_iso8601_utc`` for an absent-or-present parameter."""
    return None if raw is None else parse_iso8601_utc(raw, param=param)
