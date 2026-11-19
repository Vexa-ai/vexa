"""event.v2 — the producers' Ed25519 keys: the one place a fact seal touches key material.

CANONICAL COPY. This file lives in `core/flows/contracts/event.v2/` and is vendored VERBATIM to the
sealing producer (`core/crm/src/crm/fact_keys.py`) and the verifier
(`core/flows/src/flows_integrations/fact_keys.py`); `scripts/parity.json` (`fact-keys`) fails the
build on any byte difference. It needs `cryptography` (Apache-2.0 OR BSD-3-Clause), which is why it
sits beside `fact_envelope.py` rather than in it: the envelope stays standard library only and takes
the two callables built here.

WHO HOLDS WHAT.
- A producer holds its OWN private key, a PKCS#8 PEM Ed25519 key in a file only it mounts
  (`openssl genpkey -algorithm ed25519`); `signer_from_file` reads it. The producer names and
  declares the env that points at the file. Without it nothing seals.
- flows-api holds only PUBLIC keys: a directory with one SubjectPublicKeyInfo PEM per producer,
  named `<producer>.pem` (`openssl pkey -in key.pem -pubout`); `checks_from_dir` reads the ones it is
  asked for, and a producer with no file there verifies nothing. flows names and declares the env
  that points at the directory.
A leaked producer key forges that producer's facts and no other's, and nothing flows holds can seal.
This module reads no environment: each service declares the one key it holds.

`python -m <package>.fact_keys keygen <private.pem> <public.pem>` writes a new keypair, or keeps an
existing private key and rewrites its public half — the deploy surfaces' one-shot.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_PRODUCER = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def load_signer(pem: bytes):
    """`sign(message) -> 64-byte signature` for a PKCS#8 PEM Ed25519 private key. Raises
    ValueError for anything else (another algorithm, an encrypted or malformed key)."""
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as e:  # noqa: BLE001 — named, never echoed: the PEM is a secret
        raise ValueError("fact seal key: not a PEM private key") from e
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("fact seal key: not an Ed25519 key")
    return key.sign


def load_check(pem: bytes):
    """`check(message, signature) -> bool` for a PEM Ed25519 public key. Raises ValueError for
    anything else."""
    try:
        key = serialization.load_pem_public_key(pem)
    except Exception as e:  # noqa: BLE001
        raise ValueError("fact verify key: not a PEM public key") from e
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("fact verify key: not an Ed25519 key")

    def check(message: bytes, signature: bytes) -> bool:
        try:
            key.verify(signature, message)
            return True
        except InvalidSignature:
            return False

    return check


def signer_from_file(path):
    """The producer's signer from the PEM file at `path`, else None (no path, unreadable, or not an
    Ed25519 private key — the producer's preflight names which)."""
    path = str(path or "").strip()
    if not path:
        return None
    try:
        return load_signer(Path(path).read_bytes())
    except (OSError, ValueError):
        return None


def checks_from_dir(directory, producers) -> dict:
    """`{producer: check}` for each of `producers` whose `<producer>.pem` in `directory` is an Ed25519
    public key. No directory, a missing file or a bad key leaves that producer out, so its envelopes
    do not verify."""
    directory = str(directory or "").strip()
    if not directory:
        return {}
    checks = {}
    for producer in producers:
        if not isinstance(producer, str) or not _PRODUCER.fullmatch(producer):
            continue
        try:
            checks[producer] = load_check((Path(directory) / f"{producer}.pem").read_bytes())
        except (OSError, ValueError):
            continue
    return checks


def public_pem(private_pem: bytes) -> bytes:
    """The SubjectPublicKeyInfo PEM of a PKCS#8 PEM Ed25519 private key."""
    load_signer(private_pem)
    key = serialization.load_pem_private_key(private_pem, password=None)
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


def keygen(private_path, public_path) -> bool:
    """Write a new Ed25519 keypair, or keep the private key already at `private_path` and rewrite
    its public half. The private key is 0600. Returns True when a new key was minted."""
    private_path, public_path = Path(private_path), Path(public_path)
    minted = False
    if not private_path.exists():
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        private_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        minted = True
    os.chmod(private_path, 0o600)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(public_pem(private_path.read_bytes()))
    os.chmod(public_path, 0o644)
    return minted


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "keygen":
        sys.exit("usage: python -m <package>.fact_keys keygen <private.pem> <public.pem>")
    print("fact seal key: " + ("minted" if keygen(sys.argv[2], sys.argv[3]) else "kept"), flush=True)
