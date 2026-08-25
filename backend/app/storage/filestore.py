from __future__ import annotations

# ruff: noqa: BLE001
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import get_settings


class FileStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """Store bytes under opaque key; return the key."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Retrieve bytes by key."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def public_uri(self, key: str) -> str:
        """Logical URI for audit (s3:// or file://)."""


class LocalFileStore(FileStore):
    def __init__(self, root: str | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.local_storage_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        self._path(key).write_bytes(data)
        return key

    async def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def public_uri(self, key: str) -> str:
        return f"file://{self._path(key).resolve()}"


class S3FileStore(FileStore):
    def __init__(self, bucket: str | None = None, region: str | None = None) -> None:
        import boto3

        settings = get_settings()
        self.bucket = bucket or settings.s3_bucket
        if not self.bucket:
            raise ValueError("s3_bucket required for S3FileStore")
        session = boto3.Session(profile_name=settings.aws_profile, region_name=region or settings.aws_region)
        self.client = session.client("s3")

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return key

    async def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    async def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def public_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"


def get_file_store() -> FileStore:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3FileStore()
    return LocalFileStore()
