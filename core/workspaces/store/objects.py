"""objects.py — the few S3 calls the workspace store makes, behind one small port.

``ObjectStore`` is get/put/delete/list on one bucket under one prefix. ``S3Objects`` is the adapter
for any S3-compatible service (AWS, Linode Object Storage, Ceph RGW, StorageGRID, ECS, MinIO):
an operator-provided endpoint, a static key, path-style addressing by default, an optional private
CA bundle, an optional region. ``MemoryObjects`` is the in-process double the tests run on; it can
be told to support conditional PUT or not, and to be down.

Nothing here depends on bucket versioning or on conditional PUT. ``self_test`` finds out whether the
store has them, so the caller can use them as defence in depth and report them on /health.
"""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional, Protocol

_KEY = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9._/@-]{0,1000}$")
_MISSING = {"NoSuchKey", "404", "NotFound"}
_PRECONDITION = {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}


class StoreUnavailable(RuntimeError):
    """The bucket did not answer, or answered with an error that is not 'missing'. Writes stop."""


class PreconditionFailed(RuntimeError):
    """A conditional PUT was refused because the object already exists."""


def check_key(key: str) -> str:
    if not _KEY.fullmatch(key or "") or ".." in key.split("/") or "//" in key:
        raise ValueError(f"not a workspace store key: {key!r}")
    return key


class ObjectStore(Protocol):
    def get(self, key: str) -> Optional[bytes]: ...
    def put(self, key: str, data: bytes, *, if_absent: bool = False) -> None: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code")
               or (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") or "")


@dataclass
class S3Settings:
    """Where the bucket is and how to reach it. ``endpoint`` empty means AWS's own; ``region`` empty
    means ``us-east-1`` for signing (what most S3-compatible stores accept)."""

    bucket: str
    endpoint: str = ""
    region: str = ""
    access_key: str = ""
    secret_key: str = ""
    path_style: bool = True
    ca_bundle: str = ""          # a PEM file path; empty = the default trust store
    prefix: str = "workspaces/"
    connect_timeout: float = 5.0
    read_timeout: float = 30.0


class S3Objects:
    """One bucket, one prefix. ``client`` is a boto3 S3 client, built lazily (tests pass a fake)."""

    def __init__(self, settings: S3Settings, client=None) -> None:
        if not settings.bucket:
            raise ValueError("the workspace store names its bucket")
        self.settings = settings
        self._prefix = settings.prefix.strip("/") + "/" if settings.prefix.strip("/") else ""
        self._client = client

    def _c(self):
        if self._client is None:
            import boto3  # noqa: PLC0415 — only a configured store loads the SDK
            from botocore.config import Config  # noqa: PLC0415

            s = self.settings
            self._client = boto3.client(
                "s3", endpoint_url=s.endpoint.rstrip("/") or None, region_name=s.region or "us-east-1",
                aws_access_key_id=s.access_key or None, aws_secret_access_key=s.secret_key or None,
                verify=s.ca_bundle or True,
                config=Config(s3={"addressing_style": "path" if s.path_style else "virtual"},
                              retries={"max_attempts": 3, "mode": "standard"},
                              connect_timeout=s.connect_timeout, read_timeout=s.read_timeout))
        return self._client

    def _k(self, key: str) -> str:
        return self._prefix + check_key(key)

    def get(self, key: str) -> Optional[bytes]:
        try:
            body = self._c().get_object(Bucket=self.settings.bucket, Key=self._k(key))["Body"]
        except Exception as exc:  # noqa: BLE001 — classified: missing is None, the rest is an outage
            if _code(exc) in _MISSING:
                return None
            raise StoreUnavailable(f"GetObject {key}: {type(exc).__name__}: {exc}") from exc
        try:
            return body.read()
        finally:
            body.close()

    def put(self, key: str, data: bytes, *, if_absent: bool = False) -> None:
        kw = {"IfNoneMatch": "*"} if if_absent else {}
        try:
            self._c().put_object(Bucket=self.settings.bucket, Key=self._k(key), Body=bytes(data), **kw)
        except Exception as exc:  # noqa: BLE001
            if if_absent and _code(exc) in _PRECONDITION:
                raise PreconditionFailed(key) from exc
            raise StoreUnavailable(f"PutObject {key}: {type(exc).__name__}: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._c().delete_object(Bucket=self.settings.bucket, Key=self._k(key))
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"DeleteObject {key}: {type(exc).__name__}: {exc}") from exc

    def list(self, prefix: str) -> list[str]:
        out: list[str] = []
        full = self._prefix + prefix
        try:
            pages = self._c().get_paginator("list_objects_v2").paginate(
                Bucket=self.settings.bucket, Prefix=full)
            for page in pages:
                out += [o["Key"][len(self._prefix):] for o in page.get("Contents") or []]
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"ListObjectsV2 {prefix}: {type(exc).__name__}: {exc}") from exc
        return sorted(out)

    def versioning(self) -> str:
        """``Enabled`` / ``Suspended`` / ``off`` / ``unknown`` (no permission or not implemented)."""
        try:
            r = self._c().get_bucket_versioning(Bucket=self.settings.bucket)
        except Exception:  # noqa: BLE001 — optional, reported, never required
            return "unknown"
        return r.get("Status") or "off"


@dataclass
class MemoryObjects:
    """An in-process bucket. ``conditional``: does PUT honour If-None-Match. ``down``: every call
    raises :class:`StoreUnavailable`. ``puts`` counts writes, for tests."""

    conditional: bool = True
    down: bool = False
    data: dict = field(default_factory=dict)
    puts: int = 0
    _mu: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _up(self, what: str) -> None:
        if self.down:
            raise StoreUnavailable(f"{what}: the bucket is down")

    def get(self, key: str) -> Optional[bytes]:
        self._up("GetObject")
        return self.data.get(check_key(key))

    def put(self, key: str, data: bytes, *, if_absent: bool = False) -> None:
        self._up("PutObject")
        with self._mu:
            if if_absent and self.conditional and check_key(key) in self.data:
                raise PreconditionFailed(key)
            self.data[check_key(key)] = bytes(data)
            self.puts += 1

    def delete(self, key: str) -> None:
        self._up("DeleteObject")
        self.data.pop(check_key(key), None)

    def list(self, prefix: str) -> list[str]:
        self._up("ListObjectsV2")
        return sorted(k for k in self.data if k.startswith(prefix))

    def versioning(self) -> str:
        return "off"


@dataclass
class SelfTest:
    ok: bool
    conditional_put: bool = False
    versioning: str = "unknown"
    error: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "conditional_put": self.conditional_put,
                "versioning": self.versioning, "error": self.error}


def self_test(objects: ObjectStore, *, where: str = "") -> SelfTest:
    """Write, read and delete a probe object, then find out whether conditional PUT works.

    Conditional PUT counts as supported only if the second ``If-None-Match: *`` PUT is refused with a
    precondition failure AND the object kept its first bytes. Anything else — a closed connection
    (Linode us-sea-1, 2026-10-17), a 501, an overwrite — means unsupported, and the Redis lock plus
    the generation number guard alone. A failure of the plain write/read/delete is returned with a
    sentence an operator can act on."""
    key = f"_probe/{uuid.uuid4().hex}"
    try:
        objects.put(key, b"probe-1")
        got = objects.get(key)
        if got != b"probe-1":
            return SelfTest(False, error=f"{where}: wrote a probe object and read back {got!r}")
    except StoreUnavailable as exc:
        return SelfTest(False, error=(
            f"{where}: the workspace store cannot write and read its bucket ({exc}). Check the "
            "endpoint URL, the bucket name, the access key's permissions (List/Get/Put/DeleteObject "
            "on the bucket), path-style addressing and the CA bundle for a private certificate."))
    conditional = False
    try:
        try:
            objects.put(key, b"probe-2", if_absent=True)
        except PreconditionFailed:
            conditional = objects.get(key) == b"probe-1"
        except StoreUnavailable:
            conditional = False
    finally:
        try:
            objects.delete(key)
        except StoreUnavailable as exc:
            return SelfTest(False, error=f"{where}: the probe object could not be deleted ({exc}); "
                                         "the key needs DeleteObject on the bucket")
    versioning = objects.versioning() if hasattr(objects, "versioning") else "unknown"
    return SelfTest(True, conditional_put=conditional, versioning=versioning)
