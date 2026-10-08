"""The credential store port and its two adapters (ADR-0039).

The broker writes every credential through `Store`: a versioned key-value store whose values are
JSON objects. A write returns the new version and a receipt id the audit log records; a read names
a version so a credential that was rotated mid-operation is never mixed with its successor.

* `LocalEncryptedStore` (the default): AES-256-GCM, one random 96-bit nonce per version, the path
  and version bound as associated data so a ciphertext cannot be replayed under another name. The
  rows live in their own SQLite file beside the broker's metadata; the key comes from a file the
  deployment supplies and never touches the state volume.
* `OpenBaoStore` (optional): the OpenBao/Vault KV v2 HTTP API, for operators who already run one.
  Its path layout is the one the 0.13.2 development harness used, so a deployment that ran the
  harness keeps its stored credentials when it moves to this service.

Nothing here logs or raises a value. A failure is a `StoreUnavailable` carrying a `kind`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import httpx

_PATH = re.compile(r"^[A-Za-z0-9_.-]{1,160}(/[A-Za-z0-9_.-]{1,160}){0,3}$")


class StoreUnavailable(Exception):
    """The store could not complete an operation. `kind`: config · transport · integrity · conflict."""

    def __init__(self, kind: str) -> None:
        super().__init__("Credential store unavailable")
        self.kind = kind


@dataclass(frozen=True)
class Record:
    data: dict
    version: int
    receipt: str


class Store(Protocol):
    name: str

    def put(self, path: str, data: dict, *, cas: Optional[int] = None) -> Record: ...

    def get(self, path: str, *, version: Optional[int] = None) -> Optional[Record]: ...

    def healthy(self) -> bool: ...


def _check_path(path: str) -> str:
    if not _PATH.fullmatch(path) or ".." in path.split("/"):
        raise StoreUnavailable("config")
    return path


def load_store_key(path: "str | Path") -> bytes:
    """The local store key: 64 hex characters (`openssl rand -hex 32`), whitespace stripped."""
    try:
        text = Path(path).read_text().strip()
        key = bytes.fromhex(text)
    except (OSError, ValueError):
        raise StoreUnavailable("config") from None
    if len(key) != 32:
        raise StoreUnavailable("config")
    return key


class LocalEncryptedStore:
    name = "local"

    def __init__(self, db_path: Path, key: bytes) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(key) != 32:
            raise StoreUnavailable("config")
        self._aead = AESGCM(key)
        self._key_id = hashlib.sha256(b"vexa-credential-store-key-id\0" + key).hexdigest()[:16]
        self._db = Path(db_path)
        self._lock = threading.Lock()
        try:
            with closing(self._connect()) as c:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS secret_versions (path TEXT NOT NULL, version INTEGER NOT NULL,"
                    " nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, key_id TEXT NOT NULL, created REAL NOT NULL,"
                    " PRIMARY KEY (path, version))"
                )
        except sqlite3.Error:
            raise StoreUnavailable("config") from None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db, timeout=10, isolation_level=None)

    @staticmethod
    def _aad(path: str, version: int) -> bytes:
        return f"credential-broker.v1\0{path}\0{version}".encode()

    def put(self, path: str, data: dict, *, cas: Optional[int] = None) -> Record:
        _check_path(path)
        plaintext = json.dumps(data, separators=(",", ":")).encode()
        with self._lock:
            try:
                with closing(self._connect()) as c:
                    c.execute("BEGIN IMMEDIATE")
                    try:
                        current = c.execute(
                            "SELECT COALESCE(MAX(version), 0) FROM secret_versions WHERE path=?", (path,)
                        ).fetchone()[0]
                        if cas is not None and cas != current:
                            raise StoreUnavailable("conflict")
                        version = current + 1
                        nonce = os.urandom(12)
                        ciphertext = self._aead.encrypt(nonce, plaintext, self._aad(path, version))
                        c.execute(
                            "INSERT INTO secret_versions VALUES (?,?,?,?,?,?)",
                            (path, version, nonce, ciphertext, self._key_id, time.time()),
                        )
                    except BaseException:
                        c.execute("ROLLBACK")
                        raise
                    c.execute("COMMIT")
            except sqlite3.Error:
                raise StoreUnavailable("transport") from None
        return Record(data=data, version=version, receipt=uuid.uuid4().hex)

    def get(self, path: str, *, version: Optional[int] = None) -> Optional[Record]:
        from cryptography.exceptions import InvalidTag

        _check_path(path)
        try:
            with closing(self._connect()) as c:
                if version is None:
                    row = c.execute(
                        "SELECT version, nonce, ciphertext FROM secret_versions WHERE path=?"
                        " ORDER BY version DESC LIMIT 1",
                        (path,),
                    ).fetchone()
                else:
                    row = c.execute(
                        "SELECT version, nonce, ciphertext FROM secret_versions WHERE path=? AND version=?",
                        (path, int(version)),
                    ).fetchone()
        except sqlite3.Error:
            raise StoreUnavailable("transport") from None
        if row is None:
            return None
        stored_version, nonce, ciphertext = row
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, self._aad(path, stored_version))
        except InvalidTag:
            # A different key, or bytes changed on disk. Never "absent": that would let a
            # tampered row read as a revoked credential.
            raise StoreUnavailable("integrity") from None
        return Record(data=json.loads(plaintext), version=stored_version, receipt=uuid.uuid4().hex)

    def healthy(self) -> bool:
        try:
            with closing(self._connect()) as c:
                c.execute("SELECT 1 FROM secret_versions LIMIT 1").fetchall()
            return True
        except sqlite3.Error:
            return False


class OpenBaoStore:
    name = "openbao"

    def __init__(self, address: str, token_file: Path, mount: str = "connections", *, transport=None) -> None:
        if not address.startswith(("https://", "http://")) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", mount):
            raise StoreUnavailable("config")
        self._address = address.rstrip("/")
        self._token_file = Path(token_file)
        self._mount = mount
        self._transport = transport

    def _request(self, method: str, path: str, *, body=None, params=None, allow_missing=False):
        try:
            token = self._token_file.read_text().strip()
        except OSError:
            raise StoreUnavailable("config") from None
        try:
            with httpx.Client(timeout=5, follow_redirects=False, transport=self._transport) as client:
                response = client.request(
                    method, f"{self._address}/v1/{self._mount}/data/{path}",
                    headers={"X-Vault-Token": token}, json=body, params=params,
                )
        except httpx.HTTPError:
            raise StoreUnavailable("transport") from None
        if allow_missing and response.status_code == 404:
            return None
        if response.status_code == 400 and body and "options" in body:
            raise StoreUnavailable("conflict")
        if response.status_code in (401, 403):
            raise StoreUnavailable("config")
        if response.status_code >= 400:
            raise StoreUnavailable("transport")
        try:
            return response.json() if response.content else {}
        except ValueError:
            raise StoreUnavailable("transport") from None

    def put(self, path: str, data: dict, *, cas: Optional[int] = None) -> Record:
        _check_path(path)
        body: dict = {"data": data}
        if cas is not None:
            body["options"] = {"cas": cas}
        result = self._request("POST", path, body=body)
        try:
            return Record(data=data, version=int(result["data"]["version"]), receipt=str(result.get("request_id", "")))
        except (KeyError, TypeError, ValueError):
            raise StoreUnavailable("transport") from None

    def get(self, path: str, *, version: Optional[int] = None) -> Optional[Record]:
        _check_path(path)
        params = {"version": str(int(version))} if version is not None else None
        result = self._request("GET", path, params=params, allow_missing=True)
        if result is None:
            return None
        try:
            payload = result["data"]
            if payload.get("data") is None:
                return None
            return Record(data=payload["data"], version=int(payload["metadata"]["version"]),
                          receipt=str(result.get("request_id", "")))
        except (KeyError, TypeError, ValueError):
            raise StoreUnavailable("transport") from None

    def healthy(self) -> bool:
        try:
            with httpx.Client(timeout=3, follow_redirects=False, transport=self._transport) as client:
                return client.get(f"{self._address}/v1/sys/health").status_code == 200
        except httpx.HTTPError:
            return False
