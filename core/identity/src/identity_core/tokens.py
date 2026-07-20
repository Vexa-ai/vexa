"""Scoped tokens — mint + validate against the `identity.v1` `ScopedToken` shape.

Derived from the real admin-api behavior (`services/admin-api/app/main.py::validate_token` +
`libs/admin-models/admin_models/token_scope.py`), reimplemented clean and DB-free:

- A token carries a `subject` (owning user id), `scopes[]` drawn from {bot, tx, browser}
  (admin-models `VALID_SCOPES`), an optional `expires_at`, and an optional `email`.
- Validation rejects an **expired** token (parent: `expires_at < datetime.now(timezone.utc)`
  → 401 "Token expired") and rejects a token **out of scope** for the capability being checked
  (parent: the request scope must intersect the token's DB scopes, else 403).

The parent's opaque `vxa_<scope>_<random>` wire string + DB lookup is the *transport*; here the
`ScopedToken` value object IS the validated identity (the seam an upstream resolver hands us).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Mirrors admin-models VALID_SCOPES. Frozen so callers can't mutate the capability vocabulary.
SCOPES: frozenset[str] = frozenset({"bot", "tx", "browser"})


class TokenError(Exception):
    """A token failed validation. `code` is a stable machine reason (mirrors identity.v1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScopedToken:
    """A minted, scope-bounded credential — the `identity.v1` ScopedToken value object."""

    subject: str
    scopes: tuple[str, ...]
    expires_at: datetime | None = None
    email: str | None = None
    issued_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("ScopedToken.subject is required")
        if not self.scopes:
            raise ValueError("ScopedToken.scopes must be non-empty")
        invalid = [s for s in self.scopes if s not in SCOPES]
        if invalid:
            raise ValueError(f"Invalid scope(s) {invalid}; valid: {sorted(SCOPES)}")

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return _aware(self.expires_at) <= _aware(now or datetime.now(timezone.utc))

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def to_contract(self) -> dict:
        """Serialize to the identity.v1 ScopedToken JSON shape (RFC3339 instants, null expiry)."""
        out: dict = {
            "subject": self.subject,
            "scopes": list(self.scopes),
            "expires_at": _iso(self.expires_at),
        }
        if self.email is not None:
            out["email"] = self.email
        if self.issued_at is not None:
            out["issued_at"] = _iso(self.issued_at)
        return out


def mint_token(
    subject: str,
    scopes: list[str] | tuple[str, ...],
    *,
    expires_at: datetime | None = None,
    email: str | None = None,
    issued_at: datetime | None = None,
) -> ScopedToken:
    """Mint a scoped token. Rejects unknown/empty scopes at mint time (parent: 422 on bad scope)."""
    return ScopedToken(
        subject=subject,
        scopes=tuple(scopes),
        expires_at=expires_at,
        email=email,
        issued_at=issued_at or datetime.now(timezone.utc),
    )


def validate_token(
    token: ScopedToken,
    *,
    required_scope: str | None = None,
    now: datetime | None = None,
) -> ScopedToken:
    """Validate a token, optionally for a required capability. Returns it on success.

    Raises TokenError:
    - `token-expired` if past `expires_at` (parent → 401 "Token expired").
    - `missing-scope` if `required_scope` is set and absent (parent → 403 scope not authorized).
    """
    if token.is_expired(now):
        raise TokenError("token-expired", "Token expired")
    if required_scope is not None and not token.has_scope(required_scope):
        raise TokenError(
            "missing-scope",
            f"Token scope {sorted(token.scopes)} not authorized for '{required_scope}'",
        )
    return token


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC (admin-api stores naive UTC) so comparisons never raise."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    aware = _aware(dt)
    # RFC3339 with a trailing Z for UTC (matches the goldens / ajv date-time format).
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
