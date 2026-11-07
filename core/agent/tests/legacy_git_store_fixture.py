"""Test-only: write a credential the way agent-api's retired local store did, so the migration
readers have something real to read. Nothing in the product can write this format any more."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _keystream(enc: bytes, n: int) -> bytes:
    out, counter = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(enc + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:n])


def seal(value: str, key: bytes) -> str:
    salt = secrets.token_bytes(16)
    enc = hmac.new(key, b"vexa.secret.enc/" + salt, hashlib.sha256).digest()
    mac = hmac.new(key, b"vexa.secret.mac/" + salt, hashlib.sha256).digest()
    plain = value.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(plain, _keystream(enc, len(plain))))
    tag = hmac.new(mac, salt + ct, hashlib.sha256).digest()
    return ".".join(("v1", _b64(salt), _b64(ct), _b64(tag)))


def put(root, name: str, value: str, *, operator_key: str = "") -> Path:
    """``<root>/.secrets/<name>.enc`` sealed under ``operator_key`` (VEXA_SECRETS_KEY) or, when
    empty, under a generated ``.master.key`` beside it — both shapes the old store produced."""
    secrets_dir = Path(root) / ".secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    if operator_key:
        key = hashlib.sha256(operator_key.encode("utf-8")).digest()
    else:
        kf = secrets_dir / ".master.key"
        if not kf.exists():
            kf.write_bytes(base64.b64encode(secrets.token_bytes(32)))
        key = hashlib.sha256(kf.read_bytes().strip()).digest()
    p = secrets_dir / f"{name}.enc"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(seal(value, key), encoding="utf-8")
    return p
