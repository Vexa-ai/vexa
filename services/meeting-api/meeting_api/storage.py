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

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            region_name=self.region,
            config=BotoConfig(signature_version="s3v4"),
        )
        logger.info(f"MinIO storage client initialized: endpoint={endpoint_url}, bucket={self.bucket}")

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
        url = self.client.generate_presigned_url(
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
