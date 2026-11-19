"""legacy_git_store.py — READ-ONLY access to the Git credential store agent-api used to keep.

Saved GitHub tokens and per-workspace deploy keys now live in the credential broker (ADR-0040,
:mod:`control_plane.git_secret_store`). Before that, agent-api kept them itself under
``<root>/.secrets/<name>.enc`` (sealed with ``VEXA_SECRETS_KEY``, or with a ``.master.key`` it had
generated beside them) and, earlier still, a token in plaintext at ``<root>/.secrets/<subject>.ghtoken``.

This module exists only so those entries can be handed to the broker: ``git_secret_store`` reads
one here, writes it to the broker, reads it back, and only then calls :func:`delete` (or deletes
one its owner cleared). There is no
write path — nothing here can seal a value or create a key — and nothing here logs a value.

Only regular files directly under ``<root>/.secrets`` are read. A symbolic link — for an entry, the
generated key, a subdirectory or ``.secrets`` itself — reads as nothing: the file is checked with
``lstat``, its resolved path must be the path it was named by, and it is opened ``O_NOFOLLOW``.

A missing, corrupt or undecryptable entry is ``None`` from :func:`get`; :func:`state` tells
"there is none" (``absent``) from "there is one this key does not open" (``unreadable``), which the
migration must never mistake for absence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import stat
from pathlib import Path
from typing import Optional

from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger(__name__)

SECRETS_DIRNAME = ".secrets"
MASTER_KEY_FILENAME = ".master.key"
ENV_KEY_NAME = "VEXA_SECRETS_KEY"   # read only to open entries written before the broker held them
_ENVELOPE_VERSION = "v1"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,128}(/[A-Za-z0-9_.@-]{1,128})*$")

ABSENT, HELD, UNREADABLE = "absent", "held", "unreadable"


def secrets_dir(root: str | Path) -> Path:
    return Path(root) / SECRETS_DIRNAME


def _secret_path(root: str | Path, name: str) -> Optional[Path]:
    if not name or not _NAME_RE.match(name) or ".." in name:
        return None
    return secrets_dir(root) / f"{name}.enc"


def plaintext_token_path(root: str | Path, name: str) -> Optional[Path]:
    """``<root>/.secrets/<subject>.ghtoken`` for ``pat/<subject>`` — the oldest, unsealed shape."""
    if not name.startswith("pat/"):
        return None
    subject = name.split("/", 1)[1]
    if not subject or not _NAME_RE.match(subject) or "/" in subject or ".." in subject:
        return None
    return secrets_dir(root) / f"{subject}.ghtoken"


def _read_regular(root: str | Path, p: Path) -> Optional[bytes]:
    """The bytes of ``p`` when it is a regular file inside ``<root>/.secrets`` reached through no
    symbolic link; ``None`` otherwise (missing, a link, a directory, a device, anything else)."""
    base = secrets_dir(root)
    try:
        if not stat.S_ISDIR(os.lstat(base).st_mode):
            return None
        rel = p.relative_to(base)
        if not stat.S_ISREG(os.lstat(p).st_mode):
            return None
        if p.resolve(strict=True) != base.resolve(strict=True).joinpath(rel):
            return None
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks = []
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _read_text(root: str | Path, p: Path) -> Optional[str]:
    raw = _read_regular(root, p)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8").strip() or None
    except UnicodeDecodeError:
        return None


def read_key(root: str | Path) -> Optional[bytes]:
    """The key the old store sealed with: ``VEXA_SECRETS_KEY`` when set, else the generated
    ``.master.key``. Never creates one."""
    supplied = (os.environ.get(ENV_KEY_NAME, "") or "").strip()
    if supplied:
        return hashlib.sha256(supplied.encode("utf-8")).digest()
    raw = (_read_regular(root, secrets_dir(root) / MASTER_KEY_FILENAME) or b"").strip()
    return hashlib.sha256(raw).digest() if raw else None


def _subkeys(key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    enc = hmac.new(key, b"vexa.secret.enc/" + salt, hashlib.sha256).digest()
    mac = hmac.new(key, b"vexa.secret.mac/" + salt, hashlib.sha256).digest()
    return enc, mac


def _keystream(enc: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(enc + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def unseal(envelope: str, key: bytes) -> Optional[str]:
    """The plaintext of an old envelope, or ``None`` when it is malformed, tampered with, or sealed
    under a different key. The MAC is checked before anything is decrypted."""
    try:
        version, salt_b64, ct_b64, tag_b64 = envelope.strip().split(".")
        if version != _ENVELOPE_VERSION:
            return None
        salt, ct, tag = _unb64(salt_b64), _unb64(ct_b64), _unb64(tag_b64)
    except (ValueError, TypeError):
        return None
    enc, mac_key = _subkeys(key, salt)
    if not hmac.compare_digest(tag, hmac.new(mac_key, salt + ct, hashlib.sha256).digest()):
        return None
    try:
        return bytes(a ^ b for a, b in zip(ct, _keystream(enc, len(ct)))).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _envelope(root: str | Path, name: str) -> Optional[str]:
    p = _secret_path(root, name)
    if p is None:
        return None
    return _read_text(root, p)


def _plaintext(root: str | Path, name: str) -> Optional[str]:
    p = plaintext_token_path(root, name)
    if p is None:
        return None
    return _read_text(root, p)


def get(root: str | Path, name: str) -> Optional[str]:
    """The old store's value for ``name`` (sealed first, then the plaintext token file), or ``None``."""
    envelope = _envelope(root, name)
    if envelope is not None:
        key = read_key(root)
        value = unseal(envelope, key) if key is not None else None
        if value is not None:
            return value
    return _plaintext(root, name)


def state(root: str | Path, name: str) -> str:
    """``absent`` · ``held`` · ``unreadable`` for ``name`` in the old store."""
    if get(root, name) is not None:
        return HELD
    return UNREADABLE if _envelope(root, name) is not None else ABSENT


def delete(root: str | Path, name: str) -> bool:
    """Remove ``name``'s old entries (sealed and plaintext). True when something was removed."""
    removed = False
    for p in (_secret_path(root, name), plaintext_token_path(root, name)):
        if p is None:
            continue
        try:
            p.unlink()
            removed = True
            wpaths.notify_removed(p)   # WS-S3
        except OSError:
            pass
    retire_key_if_empty(root)
    return removed


def pending(root: str | Path) -> list[Path]:
    """Every old entry still on disk under ``root`` — sealed ``*.enc`` and plaintext ``*.ghtoken``."""
    d = secrets_dir(root)
    try:
        if not stat.S_ISDIR(os.lstat(d).st_mode):
            return []
    except OSError:
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(d, followlinks=False):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix not in (".enc", ".ghtoken"):
                continue
            try:
                if stat.S_ISREG(os.lstat(p).st_mode):
                    out.append(p)
            except OSError:
                pass
    return sorted(out)


def has_entry(root: str | Path, name: str) -> bool:
    """Is there anything on disk under ``name``'s old paths (a link counts)?"""
    return any(p is not None and os.path.lexists(p)
               for p in (_secret_path(root, name), plaintext_token_path(root, name)))


def retire_key_if_empty(root: str | Path) -> bool:
    """Once nothing is left to open, the generated ``.master.key`` and the empty store directory
    go too. True when the key was removed."""
    d = secrets_dir(root)
    if not d.is_dir() or pending(root):
        return False
    gone = False
    try:
        (d / MASTER_KEY_FILENAME).unlink()
        gone = True
        wpaths.notify_removed(d / MASTER_KEY_FILENAME)   # WS-S3
    except OSError:
        pass
    for sub in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            sub.rmdir()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass
    return gone
