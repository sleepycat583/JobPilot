from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Iterator, Protocol

from app.core.config import Settings


class BlobStore(Protocol):
    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> None: ...

    def materialize(self, key: str) -> Iterator[Path]: ...

    def probe(self) -> None: ...


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Blob key escapes local storage root")
        return candidate

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> None:
        del content_type
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def probe(self) -> None:
        if not self.root.is_dir():
            raise RuntimeError("Local blob storage directory is unavailable")

    @contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        # Absolute paths are accepted only for recovery of jobs created before
        # the BlobStore key contract was introduced.
        path = Path(key) if Path(key).is_absolute() else self._safe_path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        yield path


class S3BlobStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.object_storage_bucket:
            raise RuntimeError("OBJECT_STORAGE_BUCKET is required when UPLOAD_STORAGE_BACKEND=s3")
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - dependency is installed in production builds
            raise RuntimeError("boto3 is required for S3 object storage") from exc

        access_key = settings.object_storage_access_key_id.get_secret_value() if settings.object_storage_access_key_id else None
        secret_key = settings.object_storage_secret_access_key.get_secret_value() if settings.object_storage_secret_access_key else None
        self._client = boto3.client(
            "s3",
            region_name=settings.object_storage_region,
            endpoint_url=settings.object_storage_endpoint_url or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        self.bucket = settings.object_storage_bucket
        self.prefix = settings.object_storage_prefix.strip("/")

    def _key(self, key: str) -> str:
        path = Path(key)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Blob key is invalid")
        clean = key.strip("/")
        return f"{self.prefix}/{clean}" if self.prefix else clean

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self._key(key),
            Body=content,
            ContentType=content_type or "application/octet-stream",
        )

    def probe(self) -> None:
        self._client.head_bucket(Bucket=self.bucket)

    @contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        suffix = Path(key).suffix
        temporary = tempfile.NamedTemporaryFile(prefix="career-agent-", suffix=suffix, delete=False)
        temporary_path = Path(temporary.name)
        temporary.close()
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._key(key))
            body = response["Body"]
            try:
                temporary_path.write_bytes(body.read())
            finally:
                body.close()
            yield temporary_path
        finally:
            temporary_path.unlink(missing_ok=True)


def build_blob_store(settings: Settings) -> BlobStore:
    if settings.upload_storage_backend == "local":
        return LocalBlobStore(settings.upload_dir)
    return S3BlobStore(settings)
