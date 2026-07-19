"""Review 审计数据访问。

本模块封装 `review_audits` 的创建与结果回写，供 API resume 边界复用；
调用方传入业务 Session，本仓储不负责创建 Engine 或管理外层依赖。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ReviewAudit


class ReviewAuditRepository:
    """`review_audits` 仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_submitted_audit(
        self,
        *,
        session_id: str,
        thread_id: str,
        review_target: str,
        action: str,
        feedback: str | None,
        command_json: str,
        checkpoint_status_before: str | None,
    ) -> ReviewAudit:
        """创建一条已提交、待完成的审计记录。"""

        entity = ReviewAudit(
            session_id=session_id,
            thread_id=thread_id,
            review_target=review_target,
            action=action,
            feedback=feedback,
            command_json=command_json,
            checkpoint_status_before=checkpoint_status_before,
            result="submitted",
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def mark_completed(
        self,
        audit_id: int,
        *,
        result: str,
        completed_at: datetime,
        result_code: str | None = None,
        error_message: str | None = None,
    ) -> ReviewAudit:
        """更新审计记录的最终执行结果。"""

        entity = self._session.get(ReviewAudit, audit_id)
        if entity is None:
            raise LookupError(f"ReviewAudit {audit_id} not found")
        entity.result = result
        entity.result_code = result_code
        entity.error_message = error_message
        entity.completed_at = completed_at
        self._session.commit()
        self._session.refresh(entity)
        return entity