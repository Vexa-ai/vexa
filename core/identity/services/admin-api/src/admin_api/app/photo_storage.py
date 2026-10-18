"""photo_storage.py — where the bytes of a person's photo live (Vexa-ai/vexa#1783, UI 15g).

Not in identity's database: every token check reads that database, and backups should not grow with
images. The bytes go to the install's object store — the same S3 service and bucket recordings use,
under their own prefix ``person-photos/<user id>/`` — and the ``person_photos`` row keeps the key,
the SHA-256 and the type. admin-api is the only writer of that prefix.

The store is configured the way meeting-api's is, from the same names: ``S3_ENDPOINT`` else
``MINIO_ENDPOINT`` (a bare ``host:port`` is http, https with ``MINIO_SECURE=true``), ``S3_ACCESS_KEY``
/ ``S3_SECRET_KEY`` else ``MINIO_ACCESS_KEY`` / ``MINIO_SECRET_KEY``, and ``MINIO_BUCKET`` (default
``vexa``). With no endpoint or key there is no store: a photo cannot be set (503) and none is shown;
initials are drawn instead.

Keys are made here only (``key_for``) and checked on every call, so nothing a caller supplies can
name an object outside the prefix.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Protocol

PREFIX = "person-photos/"
_KEY = re.compile(r"^person-photos/[0-9]{1,12}/(upload|provider|gravatar)-[0-9a-f]{16}\.(png|jpg|webp)$")
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MISSING = ("NoSuchKey", "404", "NotFound")


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> Optional[bytes]: ...
    def delete(self, key: str) -> None: ...
    def delete_person(self, user_id: int) -> int: ...


def key_for(user_id: int, source: str, sha256: str, content_type: str) -> str:
    key = f"{PREFIX}{int(user_id)}/{source}-{sha256[:16]}.{_EXT[content_type]}"
    return check_key(key)


def check_key(key: str) -> str:
    if not _KEY.fullmatch(key or ""):
        raise ValueError("not a person-photo key")
    return key


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code")
               or (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") or "")


class S3Objects:
    """Person photos in one S3 bucket, under ``PREFIX``. ``client`` is a boto3 S3 client (built
    lazily when not given — tests pass a fake)."""

    def __init__(self, *, bucket: str, endpoint: str = "", access_key: str = "", secret_key: str = "",
                 client=None) -> None:
        self._bucket = bucket
        self._endpoint, self._ak, self._sk = endpoint, access_key, secret_key
        self._client = client

    def _c(self):
        if self._client is None:
            import boto3  # noqa: PLC0415 — only a configured store loads the SDK
            from botocore.config import Config  # noqa: PLC0415

            self._client = boto3.client(
                "s3", endpoint_url=self._endpoint or None, aws_access_key_id=self._ak or None,
                aws_secret_access_key=self._sk or None,
                config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3},
                              connect_timeout=5, read_timeout=15))
        return self._client

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._c().put_object(Bucket=self._bucket, Key=check_key(key), Body=bytes(data), ContentType=content_type)

    def get(self, key: str) -> Optional[bytes]:
        try:
            body = self._c().get_object(Bucket=self._bucket, Key=check_key(key))["Body"]
        except Exception as exc:  # noqa: BLE001 — a missing object is None; the rest raise
            if _code(exc) in _MISSING:
                return None
            raise
        try:
            return body.read()
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self._c().delete_object(Bucket=self._bucket, Key=check_key(key))

    def delete_person(self, user_id: int) -> int:
        """Every object under this person's prefix, removed. Returns how many."""
        prefix = f"{PREFIX}{int(user_id)}/"
        n, token = 0, None
        while True:
            kw = {"Bucket": self._bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
            page = self._c().list_objects_v2(**kw)
            keys = [o["Key"] for o in page.get("Contents") or [] if str(o.get("Key", "")).startswith(prefix)]
            if keys:
                self._c().delete_objects(Bucket=self._bucket, Delete={"Objects": [{"Key": k} for k in keys]})
                n += len(keys)
            if not page.get("IsTruncated"):
                return n
            token = page.get("NextContinuationToken")


def _endpoint() -> str:
    ep = (os.getenv("S3_ENDPOINT") or "").strip()
    if ep:
        return ep
    ep = (os.getenv("MINIO_ENDPOINT") or "").strip()
    if ep and "://" not in ep:
        secure = (os.getenv("MINIO_SECURE") or "false").strip().lower() == "true"
        ep = ("https://" if secure else "http://") + ep
    return ep


_configured: dict = {}


def from_env() -> Optional[ObjectStore]:
    """The install's store, or None when none is configured. Built once per process."""
    if "store" in _configured:
        return _configured["store"]
    ep = _endpoint()
    ak = (os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY") or "").strip()
    sk = (os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY") or "").strip()
    bucket = (os.getenv("MINIO_BUCKET") or "vexa").strip()
    store = S3Objects(bucket=bucket, endpoint=ep, access_key=ak, secret_key=sk) if ep and ak and sk else None
    _configured["store"] = store
    return store


def set_for_tests(store: Optional[ObjectStore]) -> None:
    """Replace the process's store (tests); ``reset_for_tests`` forgets it."""
    _configured["store"] = store


def reset_for_tests() -> None:
    _configured.clear()
