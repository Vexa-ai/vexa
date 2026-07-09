"""Token prefix scoping — derived from `libs/admin-models/admin_models/token_scope.py`.

Format: vxa_<scope>_<random>. Valid scopes are {bot, tx, browser}. Tokens without the vxa_
prefix are legacy (full access). The DB `api_tokens.scopes` column is authoritative; the prefix
is a hint used at generation time + backfill.
"""
import re
import secrets
import string
from typing import Optional, Set

TOKEN_PREFIX = "vxa"
TOKEN_PATTERN = re.compile(r"^vxa_([a-z]+)_(.+)$")

VALID_SCOPES: Set[str] = {"bot", "tx", "browser"}


def generate_prefixed_token(scope: str, length: int = 32) -> str:
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}', must be one of {sorted(VALID_SCOPES)}")
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"{TOKEN_PREFIX}_{scope}_{random_part}"


def parse_token_scope(token: str) -> Optional[str]:
    m = TOKEN_PATTERN.match(token)
    return m.group(1) if m else None
