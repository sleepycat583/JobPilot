"""简历版本与上传幂等数据访问。

本模块只操作业务 SQLite 中的简历版本、展示版本计数器和上传幂等记录，供后续
上传 Service 和索引任务复用；不读取文件、不调用 Chroma，也不启动后台任务。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ResumeUploadIdempotencyRecord, ResumeVersion


UPLOAD_IDEMPOTENCY_RETENTION = timedelta(hours=24)


class ResumeUploadIdempotencyConflictError(ValueError):
    """表示同一上传幂等键被用于不同文件请求。"""


class ResumeIndexStateConflictError(ValueError):
    """表示不允许的简历索引状态迁移。"""


class ResumeVersionRepository:
    """`resume_versions` 与上传幂等表仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_version(
        self,
        *,
        resume_id: str,
        file_name: str,
        file_size: int,
        storage_path: str,
        idempotency_key: str,
        request_fingerprint: str,
        now: datetime | None = None,
    ) -> ResumeVersion:
        """原子分配展示版本并创建简历与上传幂等记录。

        参数：
            resume_id: 服务端生成的 UUIDv4 资源标识。
            storage_path: 相对于 `data/resumes/` 的原始文件路径。
            idempotency_key/request_fingerprint: 上传请求的去重身份与内容摘要。
            now: 可注入当前时间，方便稳定测试。
        返回：
            新创建且状态为 `pending` 的简历版本实体。

        同一事务中先原子递增单行计数器再插入实体。SQLite 对写事务串行化，
        `UPDATE ... RETURNING` 可避免“查询最大值再加一”的并发竞争。
        """

        current = _utc_now(now)
        existing = self.get_upload_idempotency(idempotency_key=idempotency_key)
        if existing is not None:
            if _is_expired(existing.expires_at, current):
                self._session.delete(existing)
                self._session.flush()
            else:
                if existing.request_fingerprint != request_fingerprint:
                    raise ResumeUploadIdempotencyConflictError("RESUME_UPLOAD_IDEMPOTENCY_KEY_REUSED")
                entity = self.get(resume_id=existing.resume_id)
                if entity is None:
                    raise RuntimeError("Resume upload idempotency record references a missing resume")
                return entity

        try:
            # `INSERT OR IGNORE` creates the singleton once; later calls leave its counter intact.
            self._session.execute(
                text("INSERT OR IGNORE INTO resume_version_counters (id, next_display_version) VALUES (1, 0)")
            )
            display_version = self._session.execute(
                text(
                    "UPDATE resume_version_counters "
                    "SET next_display_version = next_display_version + 1 "
                    "WHERE id = 1 RETURNING next_display_version"
                )
            ).scalar_one()
            entity = ResumeVersion(
                resume_id=resume_id,
                display_version=int(display_version),
                file_name=file_name,
                file_size=file_size,
                storage_path=storage_path,
                index_status="pending",
                created_at=current,
                updated_at=current,
            )
            self._session.add(entity)
            # SQLite 外键检查在插入时执行；先持久化被引用的简历行，再创建幂等记录。
            self._session.flush()
            self._session.add(
                ResumeUploadIdempotencyRecord(
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    resume_id=resume_id,
                    created_at=current,
                    expires_at=current + UPLOAD_IDEMPOTENCY_RETENTION,
                )
            )
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            existing = self.get_upload_idempotency(idempotency_key=idempotency_key)
            if existing is not None and existing.request_fingerprint != request_fingerprint:
                raise ResumeUploadIdempotencyConflictError("RESUME_UPLOAD_IDEMPOTENCY_KEY_REUSED") from error
            if existing is not None:
                entity = self.get(resume_id=existing.resume_id)
                if entity is not None:
                    return entity
            raise
        self._session.refresh(entity)
        return entity

    def get(self, *, resume_id: str) -> ResumeVersion | None:
        """按 UUIDv4 简历资源标识读取版本。"""

        return self._session.get(ResumeVersion, resume_id)

    def list_versions(self) -> list[ResumeVersion]:
        """按展示版本倒序读取全部简历，供长期简历库展示。"""

        return list(
            self._session.scalars(select(ResumeVersion).order_by(ResumeVersion.display_version.desc()))
        )

    def mark_indexing(self, *, resume_id: str, now: datetime | None = None) -> ResumeVersion:
        """将等待或失败的简历切换为索引中。

        参数：
            resume_id: 需要开始索引的简历资源 UUIDv4。
            now: 可注入当前时间，方便稳定测试。
        返回：
            已更新为 `indexing` 的实体。
        """

        entity = self._require(resume_id)
        if entity.index_status not in {"pending", "failed"}:
            raise ResumeIndexStateConflictError("RESUME_INDEX_CONFLICT")
        entity.index_status = "indexing"
        entity.error_code = None
        entity.error_message = None
        entity.updated_at = _utc_now(now)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def mark_indexed(self, *, resume_id: str, now: datetime | None = None) -> ResumeVersion:
        """将索引中的简历标记为可检索完成状态。"""

        entity = self._require(resume_id)
        if entity.index_status != "indexing":
            raise ResumeIndexStateConflictError("RESUME_INDEX_CONFLICT")
        entity.index_status = "indexed"
        entity.error_code = None
        entity.error_message = None
        entity.updated_at = _utc_now(now)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def mark_failed(
        self, *, resume_id: str, error_code: str, error_message: str, now: datetime | None = None
    ) -> ResumeVersion:
        """记录索引失败原因，使用户可在后续流程中发起重试。"""

        entity = self._require(resume_id)
        if entity.index_status not in {"pending", "indexing"}:
            raise ResumeIndexStateConflictError("RESUME_INDEX_CONFLICT")
        entity.index_status = "failed"
        entity.error_code = error_code
        entity.error_message = error_message
        entity.updated_at = _utc_now(now)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def get_upload_idempotency(
        self, *, idempotency_key: str
    ) -> ResumeUploadIdempotencyRecord | None:
        """按上传幂等键读取记录；创建新版本时会惰性清理已过期记录。"""

        return self._session.scalar(
            select(ResumeUploadIdempotencyRecord).filter_by(idempotency_key=idempotency_key)
        )

    def _require(self, resume_id: str) -> ResumeVersion:
        entity = self.get(resume_id=resume_id)
        if entity is None:
            raise LookupError(f"ResumeVersion {resume_id} not found")
        return entity


def _utc_now(value: datetime | None) -> datetime:
    """规范化可注入时间，确保持久化时间使用 UTC aware datetime。"""

    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    """兼容 SQLite 返回 naive 时间的幂等过期比较。"""

    normalized = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
    return normalized <= now