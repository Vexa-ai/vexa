"""The session store writer — meeting-api's own credentials, the bots' session location.

The bots' ``BOT_S3_*`` pair is read-only, so the write-back is stored with the storage credentials
meeting-api already holds for recordings (``S3_ACCESS_KEY`` / ``S3_SECRET_KEY``, else
``MINIO_ACCESS_KEY`` / ``MINIO_SECRET_KEY``), at ``BOT_S3_ENDPOINT`` / ``BOT_S3_BUCKET`` — the store
the bots restore from. On Compose that is the bundled storage and its root pair; an operator whose
userdata store is a different S3 gives meeting-api credentials that can write that prefix.

boto3 is imported lazily (it ships in the image, not in the test venv); every call runs off the
event loop.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol


class SessionWriter(Protocol):
    async def put(self, key: str, data: bytes) -> None:
        """Store ``data`` at ``key`` in the session bucket."""
        ...


class S3SessionWriter:
    """``SessionWriter`` over boto3."""

    def __init__(self, *, endpoint_url: str, bucket: str,
                 access_key: Optional[str], secret_key: Optional[str]):
        self._endpoint = endpoint_url
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = None

    @classmethod
    def from_env(cls, *, endpoint_url: str, bucket: str) -> "S3SessionWriter":
        return cls(
            endpoint_url=endpoint_url,
            bucket=bucket,
            access_key=os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY"),
            secret_key=os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY"),
        )

    def _c(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3", endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key, aws_secret_access_key=self._secret_key,
            )
        return self._client

    async def put(self, key: str, data: bytes) -> None:
        import asyncio

        await asyncio.to_thread(self._c().put_object, Bucket=self._bucket, Key=key, Body=data,
                                ContentType="application/octet-stream")
