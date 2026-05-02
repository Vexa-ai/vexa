"""
Storage client abstraction for Vexa recording media files.

Supports MinIO (S3-compatible) and local filesystem backends.
MinIO is the default for development (Docker Compose) and production.
Local filesystem is available for testing without object storage.
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class StorageClient(ABC):
    """Abstract interface for object storage operations."""

    @abstractmethod
    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload data to storage. Returns the storage path."""
        ...

    @abstractmethod
    def download_file(self, path: str) -> bytes:
        """Download file from storage. Returns file content as bytes."""
        ...

    @abstractmethod
    def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        """Generate a presigned download URL. expires is in seconds."""
        ...

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Delete a file from storage."""
        ...

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if a file exists in storage."""
        ...

    @abstractmethod
    def list_objects(self, prefix: str) -> list:
        """List storage paths under prefix. Returns a sorted list of full paths.

        Used by recording_finalizer to enumerate per-session chunks under
        `recordings/<user>/<rec>/<session>/<media_type>/`. Sorted ascending
        so byte-concat order matches the chunk_seq order.
        """
        ...


class MinIOStorageClient(StorageClient):
    """MinIO/S3-compatible storage client using boto3."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        secure: Optional[bool] = None,
    ):
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError:
            raise ImportError("boto3 is required for MinIO storage. Install it: pip install boto3")

        self.endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000") if endpoint is None else endpoint
        self.access_key = os.environ.get("MINIO_ACCESS_KEY", "vexa-access-key") if access_key is None else access_key
        self.secret_key = os.environ.get("MINIO_SECRET_KEY", "vexa-secret-key") if secret_key is None else secret_key
        self.bucket = bucket or os.environ.get("MINIO_BUCKET", "vexa-recordings")
        if secure is None:
            self.secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        else:
            self.secure = secure
        self.region = os.environ.get("AWS_REGION", "us-east-1")

        protocol = "https" if self.secure else "http"
        if self.endpoint:
            endpoint_url = self.endpoint if "://" in self.endpoint else f"{protocol}://{self.endpoint}"
        else:
            endpoint_url = None

        # v0.10.5.3 Pack D-3 follow-up (Option B):
        # MINIO_ENDPOINT is the cluster-internal hostname used for server-side
        # I/O (put_object/get_object) — fast, no NAT traversal.
        # MINIO_PUBLIC_ENDPOINT is what the BROWSER must use to fetch a
        # presigned URL — typically a NodePort, ingress, or host-mapped port.
        # When unset, falls back to MINIO_ENDPOINT (correct for compose/lite
        # where the bridge network DNS resolves the same hostname externally
        # via published ports). When set on helm with cluster-internal-only
        # MinIO Service, points at the externally reachable surface.
        # Pre-fix: presigned URLs always carried the internal hostname; on
        # helm with ClusterIP-only MinIO, browsers got DNS-unresolvable URLs
        # and audio playback hung at "Preparing audio...".
        public_endpoint_raw = (os.environ.get("MINIO_PUBLIC_ENDPOINT") or "").strip() or self.endpoint
        if public_endpoint_raw:
            public_endpoint_url = public_endpoint_raw if "://" in public_endpoint_raw else f"{protocol}://{public_endpoint_raw}"
        else:
            public_endpoint_url = endpoint_url
        self.public_endpoint_url = public_endpoint_url

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            region_name=self.region,
            config=BotoConfig(signature_version="s3v4"),
        )
        # Separate signing client only differs in endpoint_url. Reuses the
        # same credentials/region/bucket. Used solely by get_presigned_url.
        # If public_endpoint_url == endpoint_url, presigned URLs work as
        # before (no behavior change for compose/lite when MINIO_PUBLIC_ENDPOINT
        # is unset).
        if public_endpoint_url != endpoint_url:
            self._signing_client = boto3.client(
                "s3",
                endpoint_url=public_endpoint_url,
                aws_access_key_id=self.access_key or None,
                aws_secret_access_key=self.secret_key or None,
                region_name=self.region,
                config=BotoConfig(signature_version="s3v4"),
            )
        else:
            self._signing_client = self.client
        logger.info(
            f"MinIO storage client initialized: endpoint={endpoint_url}, "
            f"public_endpoint={public_endpoint_url}, bucket={self.bucket}"
        )

    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=path,
            Body=data,
            ContentType=content_type,
        )
        logger.info(f"Uploaded {len(data)} bytes to {self.bucket}/{path}")
        return path

    def download_file(self, path: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=path)
        data = response["Body"].read()
        logger.info(f"Downloaded {len(data)} bytes from {self.bucket}/{path}")
        return data

    def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        # v0.10.5.3 Pack D-3 follow-up (Option B): sign against the PUBLIC
        # endpoint (browser-reachable) rather than the internal endpoint
        # used for server-side I/O. See __init__ for the rationale.
        url = self._signing_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": path},
            ExpiresIn=expires,
        )
        return url

    def delete_file(self, path: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=path)
        logger.info(f"Deleted {self.bucket}/{path}")

    def file_exists(self, path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=path)
            return True
        except self.client.exceptions.ClientError:
            return False

    def list_objects(self, prefix: str) -> list:
        # Paginate so we don't truncate at 1000 (S3 default page size).
        # Sorted ascending so callers can rely on chunk_seq lexicographic
        # ordering (zero-padded 6-digit seq numbers sort correctly).
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        keys.sort()
        return keys


class LocalStorageClient(StorageClient):
    """Filesystem-based storage client for development/testing."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.environ.get("LOCAL_STORAGE_DIR", "/tmp/vexa-recordings")
        self.fsync_enabled = os.environ.get("LOCAL_STORAGE_FSYNC", "true").lower() == "true"
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"Local storage client initialized: base_dir={self.base_dir}, fsync={self.fsync_enabled}")

    def _normalize_path(self, path: str) -> str:
        # Normalize storage key and reject path traversal.
        normalized = os.path.normpath(path.replace("\\", "/")).lstrip("/")
        if normalized in ("", ".", "..") or normalized.startswith("../"):
            raise ValueError(f"Invalid storage path: {path}")
        return normalized

    def _full_path(self, path: str, create_dirs: bool = False) -> str:
        normalized = self._normalize_path(path)
        full = os.path.join(self.base_dir, normalized)
        if create_dirs:
            os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        full_path = self._full_path(path, create_dirs=True)
        with open(full_path, "wb") as f:
            f.write(data)
            f.flush()
            if self.fsync_enabled:
                os.fsync(f.fileno())
        logger.info(f"Stored {len(data)} bytes to {full_path}")
        return self._normalize_path(path)

    def download_file(self, path: str) -> bytes:
        full_path = self._full_path(path)
        with open(full_path, "rb") as f:
            return f.read()

    def get_presigned_url(self, path: str, expires: int = 3600) -> str:
        # Local storage doesn't support presigned URLs — return a file:// URI
        return f"file://{self._full_path(path)}"

    def delete_file(self, path: str) -> None:
        full_path = self._full_path(path)
        if os.path.exists(full_path):
            os.remove(full_path)
            logger.info(f"Deleted {full_path}")

    def file_exists(self, path: str) -> bool:
        return os.path.exists(self._full_path(path))

    def list_objects(self, prefix: str) -> list:
        # Walk the local filesystem under the prefix directory; return
        # storage-relative paths (forward-slash) so callers can treat them
        # interchangeably with MinIO keys.
        normalized_prefix = self._normalize_path(prefix) if prefix else ""
        full_prefix = os.path.join(self.base_dir, normalized_prefix) if normalized_prefix else self.base_dir
        keys = []
        if not os.path.isdir(full_prefix):
            return keys
        for root, _dirs, files in os.walk(full_prefix):
            for fname in files:
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(full_path, self.base_dir).replace("\\", "/")
                keys.append(rel)
        keys.sort()
        return keys


def create_storage_client(backend: Optional[str] = None) -> StorageClient:
    """Factory function to create the appropriate storage client based on configuration."""
    backend = backend or os.environ.get("STORAGE_BACKEND", "minio")

    if backend == "minio":
        return MinIOStorageClient()
    elif backend == "s3":
        return MinIOStorageClient(
            endpoint=os.environ.get("S3_ENDPOINT", ""),
            access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            bucket=os.environ.get("S3_BUCKET", os.environ.get("MINIO_BUCKET", "vexa-recordings")),
            secure=os.environ.get("S3_SECURE", "true").lower() == "true",
        )
    elif backend == "local":
        return LocalStorageClient()
    else:
        raise ValueError(f"Unknown storage backend: {backend}. Supported: minio, s3, local")
