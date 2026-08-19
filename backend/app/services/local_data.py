"""Recoverable backup and restore helpers for the single-user local runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.core.config import Settings


BACKUP_VERSION = 1


@dataclass(frozen=True)
class LocalDataPaths:
    database: Path
    checkpoint: Path
    uploads: Path
    chroma: Path


def _sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("本地备份仅支持 SQLite 业务库")
    value = database_url[len("sqlite:///") :]
    path = Path(value)
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def local_data_paths(settings: Settings) -> LocalDataPaths:
    if settings.upload_storage_backend != "local":
        raise ValueError("本地备份要求 UPLOAD_STORAGE_BACKEND=local")
    if settings.checkpoint_backend != "sqlite":
        raise ValueError("本地备份要求 CHECKPOINT_BACKEND=sqlite")
    if settings.chroma_backend != "local":
        raise ValueError("本地备份要求 CHROMA_BACKEND=local")
    return LocalDataPaths(
        database=_sqlite_path(settings.database_url),
        checkpoint=settings.graph_checkpoint_path.resolve(),
        uploads=settings.upload_dir.resolve(),
        chroma=settings.chroma_persist_directory.resolve(),
    )


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)


def backup_local_data(paths: LocalDataPaths, archive_path: Path) -> dict[str, Any]:
    """Create a consistent zip backup without exposing configured paths."""

    archive_path = archive_path.resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="career-agent-backup-") as temporary:
        root = Path(temporary)
        if paths.database.is_file():
            _sqlite_snapshot(paths.database, root / "sqlite" / "business.db")
            entries.append({"kind": "sqlite", "name": "business", "path": "sqlite/business.db"})
        if paths.checkpoint.is_file():
            _sqlite_snapshot(paths.checkpoint, root / "sqlite" / "checkpoint.db")
            entries.append({"kind": "sqlite", "name": "checkpoint", "path": "sqlite/checkpoint.db"})
        if paths.uploads.is_dir():
            _copy_tree(paths.uploads, root / "files" / "uploads")
            entries.append({"kind": "directory", "name": "uploads", "path": "files/uploads"})
        if paths.chroma.is_dir():
            _copy_tree(paths.chroma, root / "files" / "chroma")
            entries.append({"kind": "directory", "name": "chroma", "path": "files/chroma"})

        manifest = {
            "format": "career-agent-local-backup",
            "version": BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entries": entries,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            for file_path in root.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(root).as_posix())
    return manifest


def _safe_member_path(root: Path, member: str) -> Path:
    path = (root / member).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("备份包含非法路径")
    return path


def _replace_target(target: Path, *, force: bool) -> None:
    if not target.exists():
        return
    if not force:
        raise FileExistsError(f"恢复目标已存在，请使用 --force: {target.name}")
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    moved = target.with_name(f"{target.name}.pre-restore-{suffix}")
    shutil.move(str(target), str(moved))


def restore_local_data(paths: LocalDataPaths, archive_path: Path, *, force: bool = False) -> dict[str, Any]:
    """Restore a backup; existing targets are moved aside when force is enabled."""

    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    with tempfile.TemporaryDirectory(prefix="career-agent-restore-") as temporary:
        root = Path(temporary)
        with ZipFile(archive_path) as archive:
            for info in archive.infolist():
                member = info.filename.replace("\\", "/")
                if not member or member.endswith("/"):
                    continue
                if ".." in Path(member).parts or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("备份包含非法文件")
                destination = _safe_member_path(root, member)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("备份缺少 manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "career-agent-local-backup" or manifest.get("version") != BACKUP_VERSION:
            raise ValueError("备份格式或版本不受支持")
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ValueError("备份清单无效")
        targets = {"business": paths.database, "checkpoint": paths.checkpoint, "uploads": paths.uploads, "chroma": paths.chroma}
        restored: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("name") not in targets or not isinstance(entry.get("path"), str):
                raise ValueError("备份清单包含未知条目")
            source = _safe_member_path(root, entry["path"])
            target = targets[entry["name"]]
            if entry.get("kind") == "sqlite":
                if not source.is_file():
                    raise ValueError("SQLite 备份文件缺失")
                _replace_target(target, force=force)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif entry.get("kind") == "directory":
                if not source.is_dir():
                    raise ValueError("目录备份内容缺失")
                _replace_target(target, force=force)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)
            else:
                raise ValueError("备份条目类型无效")
            restored.append(str(entry["name"]))
    return {"version": BACKUP_VERSION, "restored": restored}
