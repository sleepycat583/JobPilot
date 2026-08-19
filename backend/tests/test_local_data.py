import sqlite3
from pathlib import Path

from app.core.config import Settings
from app.services.local_data import backup_local_data, local_data_paths, restore_local_data


def test_local_backup_and_restore_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    checkpoint = tmp_path / "langgraph.db"
    uploads = tmp_path / "uploads"
    chroma = tmp_path / "chroma"
    uploads.mkdir()
    chroma.mkdir()
    connection = sqlite3.connect(database)
    connection.execute("create table records (value text)")
    connection.execute("insert into records values ('before backup')")
    connection.commit()
    connection.close()
    connection = sqlite3.connect(checkpoint)
    connection.execute("create table checkpoints (value text)")
    connection.execute("insert into checkpoints values ('checkpoint')")
    connection.commit()
    connection.close()
    (uploads / "resume.txt").write_text("resume", encoding="utf-8")
    (chroma / "index.bin").write_bytes(b"vector")

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{database}",
        graph_checkpoint_path=checkpoint,
        upload_dir=uploads,
        chroma_persist_directory=chroma,
    )
    archive = tmp_path / "backup.zip"
    manifest = backup_local_data(local_data_paths(settings), archive)
    assert {entry["name"] for entry in manifest["entries"]} == {"business", "checkpoint", "uploads", "chroma"}

    connection = sqlite3.connect(database)
    connection.execute("delete from records")
    connection.commit()
    connection.close()
    (uploads / "resume.txt").write_text("changed", encoding="utf-8")
    result = restore_local_data(local_data_paths(settings), archive, force=True)
    assert set(result["restored"]) == {"business", "checkpoint", "uploads", "chroma"}
    connection = sqlite3.connect(database)
    assert connection.execute("select value from records").fetchone() == ("before backup",)
    connection.close()
    assert (uploads / "resume.txt").read_text(encoding="utf-8") == "resume"
    assert (chroma / "index.bin").read_bytes() == b"vector"


def test_local_backup_rejects_shared_backends(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, upload_storage_backend="s3")
    try:
        local_data_paths(settings)
    except ValueError as exc:
        assert "UPLOAD_STORAGE_BACKEND" in str(exc)
    else:
        raise AssertionError("shared storage should not be backed up by local archive")
