"""
Token prefix scoping for Vexa API tokens.

Format: vxa_<scope>_<random>
Example: vxa_bot_a7f2b9c1d3e5f6...

Scopes:
  - user: Dashboard/webapp tokens (SSO login)
  - bot: Bot management tokens
  - tx: Transcription API tokens
  - admin: Full admin access

Tokens without the vxa_ prefix are legacy (full access).
"""

import re
import secrets
import string
from typing import Optional, Set

TOKEN_PREFIX = "vxa"
TOKEN_PATTERN = re.compile(r"^vxa_([a-z]+)_(.+)$")

VALID_SCOPES = {"user", "bot", "tx", "admin"}


def generate_prefixed_token(scope: str, length: int = 32) -> str:
    """Generate a token with prefix: vxa_<scope>_<random>."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}', must be one of {VALID_SCOPES}")
    alphabet = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(length))
    return f"{TOKEN_PREFIX}_{scope}_{random_part}"


def parse_token_scope(token: str) -> Optional[str]:
    """Parse scope from a prefixed token. Returns None for legacy tokens."""
    match = TOKEN_PATTERN.match(token)
    if not match:
        return None
    return match.group(1)


def check_token_scope(token: str, allowed_scopes: Set[str]) -> bool:
    """Check if a token's scope is in the allowed set.

    Legacy tokens (no prefix) are always allowed for backward compatibility.
    """
    scope = parse_token_scope(token)
    if scope is None:
        return True  # Legacy token — full access
    return scope in allowed_scopes
