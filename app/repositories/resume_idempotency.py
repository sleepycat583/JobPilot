"""HITL resume 幂等记录访问。

本模块只操作业务 SQLite 中的 `resume_idempotency_records`，供 API resume
边界占用、重放与完成回写；不读取或修改 LangGraph Checkpoint。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ResumeIdempotencyRecord


RESUME_LEASE_SECONDS = 30


class ResumeIdempotencyConflictError(ValueError):
    """表示同一线程的显式幂等键已被其他请求占用。"""


class ResumeIdempotencyRepository:
    """`resume_idempotency_records` 仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_processing(
        self,
        *,
        thread_id: str,
        session_id: str,
        idempotency_key: str,
        command_fingerprint: str,
        now: datetime | None = None,
    ) -> ResumeIdempotencyRecord:
        """创建 30 秒处理租约。

        参数：
            thread_id/idempotency_key: 共同构成数据库唯一去重键。
            command_fingerprint: 命令规范 JSON 的摘要，用于拒绝同 key 换用途。
            now: 可注入当前时间，方便稳定测试。
        返回：
            已持久化且状态为 `processing` 的记录。
        """

        current = _utc_now(now)
        entity = ResumeIdempotencyRecord(
            thread_id=thread_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            command_fingerprint=command_fingerprint,
            status="processing",
            lease_expires_at=current + timedelta(seconds=RESUME_LEASE_SECONDS),
            created_at=current,
        )
        self._session.add(entity)
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise ResumeIdempotencyConflictError("RESUME_IDEMPOTENCY_CONFLICT") from error
        self._session.refresh(entity)
        return entity

    def get(self, *, thread_id: str, idempotency_key: str) -> ResumeIdempotencyRecord | None:
        """按显式幂等键读取已存在的处理记录。"""

        return self._session.scalar(
            select(ResumeIdempotencyRecord).filter_by(thread_id=thread_id, idempotency_key=idempotency_key)
        )

    def reclaim_expired_lease(
        self, entity: ResumeIdempotencyRecord, *, now: datetime | None = None
    ) -> bool:
        """在处理租约到期时重新取得执行权。

        返回值：
            租约已到期并被更新时返回 `True`；未到期或非处理中记录返回 `False`。
        """

        current = _utc_now(now)
        if entity.status != "processing" or not _lease_is_expired(entity.lease_expires_at, current):
            return False
        entity.lease_expires_at = current + timedelta(seconds=RESUME_LEASE_SECONDS)
        entity.error_code = "RESUME_LEASE_EXPIRED"
        entity.error_message = "Previous resume processing lease expired; execution was reclaimed"
        self._session.commit()
        self._session.refresh(entity)
        return True

    def attach_review_audit(self, entity: ResumeIdempotencyRecord, *, review_audit_id: int) -> ResumeIdempotencyRecord:
        """关联本次实际执行创建的 Review 审计记录。"""

        entity.review_audit_id = review_audit_id
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def mark_succeeded(
        self,
        entity: ResumeIdempotencyRecord,
        *,
        http_status: int,
        response_json: str,
        review_audit_id: int | None,
        now: datetime | None = None,
    ) -> ResumeIdempotencyRecord:
        """保存首次成功 resume 的可重放 HTTP 响应快照。"""

        entity.status = "succeeded"
        entity.lease_expires_at = None
        entity.http_status = http_status
        entity.response_json = response_json
        entity.review_audit_id = review_audit_id
        entity.completed_at = _utc_now(now)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def mark_failed(
        self,
        entity: ResumeIdempotencyRecord,
        *,
        error_code: str,
        error_message: str,
        review_audit_id: int | None,
        now: datetime | None = None,
    ) -> ResumeIdempotencyRecord:
        """记录首次图恢复失败，供相同 key 返回稳定错误。"""

        entity.status = "failed"
        entity.lease_expires_at = None
        entity.error_code = error_code
        entity.error_message = error_message
        entity.review_audit_id = review_audit_id
        entity.completed_at = _utc_now(now)
        self._session.commit()
        self._session.refresh(entity)
        return entity


def _utc_now(value: datetime | None) -> datetime:
    """规范化可注入时间，确保租约比较使用 UTC aware datetime。"""

    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _lease_is_expired(lease_expires_at: datetime | None, now: datetime) -> bool:
    """判断处理租约是否已过期，缺失租约按可恢复异常处理。"""

    if lease_expires_at is None:
        return True
    normalized = lease_expires_at if lease_expires_at.tzinfo is not None else lease_expires_at.replace(tzinfo=timezone.utc)
    return normalized <= now