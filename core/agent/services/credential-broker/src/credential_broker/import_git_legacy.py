"""One-shot import: Git credentials agent-api kept on its own volume move into this broker's store.

Saved GitHub tokens and deploy keys live here, under ``git/<name>`` (the `/api/internal/git-secret`
route). Older agent-api builds kept them in their workspaces volume, under ``.secrets/``: sealed
``<name>.enc`` files (keyed by that build's ``VEXA_SECRETS_KEY``, or by a ``.master.key`` generated
beside them) and, older still, plaintext ``<subject>.ghtoken`` tokens. agent-api moves each one on its
owner's next Git operation; this command moves them all at once::

    python -m credential_broker.import_git_legacy /legacy/.secrets [--operator-key-file PATH|-]

run in the broker's own image with the broker's environment and agent-api's workspaces volume mounted
at ``/legacy`` (``--operator-key-file -`` reads the old ``VEXA_SECRETS_KEY`` from stdin).

* **Idempotent.** A name the broker already holds — a value or a tombstone — is never overwritten;
  only the stale old copy is removed. A second run finds nothing to do.
* **Remove only after read-back.** Each value is written, read back from the store, compared, and
  only then is its old file deleted. An entry the key cannot open is kept and counted.
* **Counts only.** Nothing here prints a name or a value.
* **Regular files only.** An entry, the generated key, a subdirectory or the directory itself that is
  a symbolic link is not read: each file is checked with ``lstat``, must resolve to the path it was
  found at, and is opened ``O_NOFOLLOW``. Handing the state back uses ``chown`` without following links.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Optional

from .store import Store, StoreUnavailable

#: The names agent-api's Git store uses (control_plane/git_secret_store.py).
NAME = re.compile(r"^(pat/[A-Za-z0-9_.-]{1,128}|deploy/(?:user|ws)-[A-Za-z0-9_.-]{1,120}\.(?:priv|pub))$")
MASTER_KEY = ".master.key"


def _is_real_dir(d: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(d).st_mode)
    except OSError:
        return False


def _read_regular(secrets_dir: Path, p: Path) -> Optional[bytes]:
    """The bytes of ``p`` when it is a regular file inside ``secrets_dir`` reached through no
    symbolic link; ``None`` otherwise."""
    try:
        if not _is_real_dir(secrets_dir):
            return None
        rel = p.relative_to(secrets_dir)
        if not stat.S_ISREG(os.lstat(p).st_mode):
            return None
        if p.resolve(strict=True) != secrets_dir.resolve(strict=True).joinpath(rel):
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


def legacy_key(secrets_dir: Path, operator_key: str = "") -> Optional[bytes]:
    """The key the old store sealed with: the operator's, else the generated ``.master.key``."""
    if operator_key.strip():
        return hashlib.sha256(operator_key.strip().encode("utf-8")).digest()
    raw = (_read_regular(secrets_dir, secrets_dir / MASTER_KEY) or b"").strip()
    return hashlib.sha256(raw).digest() if raw else None


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def open_envelope(envelope: str, key: bytes) -> Optional[str]:
    """Read one old envelope (``v1.<salt>.<ciphertext>.<mac>``); ``None`` unless the MAC verifies."""
    try:
        version, salt_b64, ct_b64, tag_b64 = envelope.strip().split(".")
        salt, ct, tag = _unb64(salt_b64), _unb64(ct_b64), _unb64(tag_b64)
    except (ValueError, TypeError):
        return None
    if version != "v1":
        return None
    enc = hmac.new(key, b"vexa.secret.enc/" + salt, hashlib.sha256).digest()
    mac = hmac.new(key, b"vexa.secret.mac/" + salt, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, hmac.new(mac, salt + ct, hashlib.sha256).digest()):
        return None
    stream = bytearray()
    counter = 0
    while len(stream) < len(ct):
        stream += hashlib.sha256(enc + counter.to_bytes(8, "big")).digest()
        counter += 1
    try:
        return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")
    except UnicodeDecodeError:
        return None


def entries(secrets_dir: Path) -> list[tuple[str, Path]]:
    """``(name, file)`` for every old entry: sealed ``*.enc`` and plaintext ``*.ghtoken``."""
    found = []
    if not _is_real_dir(secrets_dir):
        return found
    paths = []
    for dirpath, _dirs, files in os.walk(secrets_dir, followlinks=False):
        paths.extend(Path(dirpath) / f for f in files)
    for p in sorted(paths):
        try:
            if not stat.S_ISREG(os.lstat(p).st_mode):
                continue
        except OSError:
            continue
        rel = p.relative_to(secrets_dir).as_posix()
        if p.suffix == ".enc":
            found.append((rel[: -len(".enc")], p))
        elif p.suffix == ".ghtoken" and "/" not in rel:
            found.append(("pat/" + rel[: -len(".ghtoken")], p))
    return found


def _tidy(secrets_dir: Path) -> None:
    """Once nothing is left to open, the generated key and the empty directories go too."""
    if entries(secrets_dir):
        return
    (secrets_dir / MASTER_KEY).unlink(missing_ok=True)
    for d in sorted((p for p in secrets_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        secrets_dir.rmdir()
    except OSError:
        pass


def run(secrets_dir: Path, store: Store, *, operator_key: str = "") -> dict:
    """Move every old entry under ``secrets_dir`` into ``store``. Returns counts."""
    counts = {"moved": 0, "already_held": 0, "unreadable": 0, "invalid": 0, "failed": 0}
    if not _is_real_dir(secrets_dir):
        return counts
    key = legacy_key(secrets_dir, operator_key)
    for name, path in entries(secrets_dir):
        if not NAME.fullmatch(name) or ".." in name:
            counts["invalid"] += 1
            continue
        target = "git/" + name
        try:
            if store.get(target) is not None:
                path.unlink(missing_ok=True)
                counts["already_held"] += 1
                continue
            raw = _read_regular(secrets_dir, path)
            if raw is None:
                counts["unreadable"] += 1
                continue
            text = raw.decode("utf-8", errors="replace").strip()
            if path.suffix == ".enc":
                value = open_envelope(text, key) if key is not None else None
            else:
                value = text or None
            if value is None:
                counts["unreadable"] += 1
                continue
            try:
                store.put(target, {"value": value}, cas=0)
            except StoreUnavailable as exc:
                if exc.kind != "conflict":
                    raise
            back = store.get(target)
            if back is None:
                counts["failed"] += 1
                continue
            path.unlink(missing_ok=True)
            counts["moved" if back.data.get("value") == value else "already_held"] += 1
        except (OSError, StoreUnavailable):
            counts["failed"] += 1
    _tidy(secrets_dir)
    return counts


def _hand_back(state_dir: Path) -> None:
    """Run as root to read agent-api's files, the import must leave the broker's state owned by the
    broker's own user, or the next boot cannot write it."""
    if os.geteuid() != 0:
        return
    st = state_dir.stat()
    paths = [state_dir]
    for dirpath, dirs, files in os.walk(state_dir, followlinks=False):
        paths.extend(Path(dirpath) / n for n in [*dirs, *files])
    for p in paths:
        try:
            os.chown(p, st.st_uid, st.st_gid, follow_symlinks=False)
        except (OSError, NotImplementedError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m credential_broker.import_git_legacy",
                                     description="Move Git credentials from agent-api's retired local store into this broker.")
    parser.add_argument("secrets_dir", type=Path, help="agent-api's <workspaces>/.secrets directory")
    parser.add_argument("--operator-key-file", default="",
                        help="file holding the old VEXA_SECRETS_KEY, or - for stdin; omit when the old store generated its key")
    args = parser.parse_args(argv)
    operator_key = ""
    if args.operator_key_file == "-":
        operator_key = sys.stdin.read()
    elif args.operator_key_file:
        operator_key = Path(args.operator_key_file).read_text()

    from .main import build_store
    from .settings import load

    settings = load()
    counts = run(args.secrets_dir, build_store(settings), operator_key=operator_key)
    _hand_back(Path(settings.state_dir))
    print(json.dumps({"event": "git_legacy_import", **counts}), flush=True)
    return 1 if counts["unreadable"] or counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
