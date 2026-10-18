"""session_blobs.py — where a session's transcript versions live (ADR-0048 decision 2).

One small port and one adapter: ``S3Blobs``, the install's object store (ADR-0038: every install has
S3; versitygw on a single machine) through boto3 — the client meeting-api uses for recordings —
with path-style addressing, because versitygw and most self-hosted stores expect it. The bucket is
the session store's own (``vexa-sessions`` by default), reached with a key scoped to that bucket
alone; it is never the recordings bucket.

Objects are immutable: a key is written once (the session store names every version with a fresh
nonce), so the adapter never needs a conditional write.
"""
from __future__ import annotations

import re
from typing import Optional, Protocol

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_MISSING = ("NoSuchKey", "404", "NotFound")


def _check_key(key: str) -> str:
    if not _KEY.fullmatch(key or "") or ".." in key.split("/") or "//" in key:
        raise ValueError("not a session object key")
    return key


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> Optional[bytes]: ...
    def delete(self, key: str) -> None: ...


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code")
               or (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") or "")


class S3Blobs:
    """Objects in one S3 bucket. ``client`` is a boto3 S3 client (built lazily from the endpoint
    and key when not given — tests pass a fake)."""

    def __init__(self, *, bucket: str, endpoint: str = "", access_key: str = "", secret_key: str = "",
                 region: str = "us-east-1", client=None) -> None:
        if not bucket:
            raise ValueError("a session object store names its bucket")
        self._bucket = bucket
        self._endpoint = endpoint.rstrip("/")
        self._ak, self._sk = access_key, secret_key
        self._region = region or "us-east-1"
        self._client = client

    def _c(self):
        if self._client is None:
            import boto3  # noqa: PLC0415 — only a configured store loads the SDK
            from botocore.config import Config  # noqa: PLC0415

            self._client = boto3.client(
                "s3", endpoint_url=self._endpoint or None, region_name=self._region,
                aws_access_key_id=self._ak or None, aws_secret_access_key=self._sk or None,
                config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3},
                              connect_timeout=5, read_timeout=30))
        return self._client

    def put(self, key: str, data: bytes) -> None:
        self._c().put_object(Bucket=self._bucket, Key=_check_key(key), Body=bytes(data),
                             ContentType="application/json")

    def get(self, key: str) -> Optional[bytes]:
        try:
            body = self._c().get_object(Bucket=self._bucket, Key=_check_key(key))["Body"]
        except Exception as exc:  # noqa: BLE001 — classified: a missing object is None, the rest raise
            if _code(exc) in _MISSING:
                return None
            raise
        try:
            return body.read()
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self._c().delete_object(Bucket=self._bucket, Key=_check_key(key))
