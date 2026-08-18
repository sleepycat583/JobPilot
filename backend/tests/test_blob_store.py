from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.blob_store import LocalBlobStore, S3BlobStore, build_blob_store


def test_local_blob_store_round_trip(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "uploads")
    store.put_bytes("resume-1.txt", b"hello", content_type="text/plain")
    with store.materialize("resume-1.txt") as path:
        assert path.read_bytes() == b"hello"
    store.probe()


def test_local_blob_store_rejects_path_escape(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "uploads")
    with pytest.raises(ValueError):
        store.put_bytes("../outside.txt", b"no", content_type="text/plain")


def test_s3_blob_store_requires_bucket() -> None:
    settings = Settings(_env_file=None, upload_storage_backend="s3")
    with pytest.raises(RuntimeError, match="OBJECT_STORAGE_BUCKET"):
        S3BlobStore(settings)


def test_local_backend_is_selected_by_default(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, upload_dir=tmp_path / "uploads")
    assert isinstance(build_blob_store(settings), LocalBlobStore)
