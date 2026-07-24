"""业务 ORM 模型定义。

本模块声明 Week3 Task 7-9 需要的业务表，供 Repository、Alembic 与测试复用；
不包含查询逻辑或运行时副作用。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExperimentRun(Base):
    """实验运行记录。

    做什么：
        持久化单次实验运行的输入版本、执行状态、统计指标与结构化输出快照。
    返回值：
        ORM 实体本身用于脚本和后续 Repository 查询。
    """

    __tablename__ = "experiment_runs"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'degraded', 'failed')", name="ck_experiment_runs_status"),
        UniqueConstraint(
            "case_name", "architecture", "model_name", "prompt_version", "run_index",
            name="uq_experiment_runs_sampling_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    architecture: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    run_index: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_valid: Mapped[bool] = mapped_column(nullable=False)
    unsupported_skill_claims: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    error_codes: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAudit(Base):
    """人工介入审计记录。

    做什么：
        记录每次 HITL 命令的会话、线程、目标、动作、反馈与执行结果，供后续复盘。
    返回值：
        ORM 实体本身用于 API 边界更新与查询。
    """

    __tablename__ = "review_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    review_target: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_json: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_status_before: Mapped[str | None] = mapped_column(String(30), nullable=True)
    result: Mapped[str] = mapped_column(String(30), nullable=False, default="submitted")
    result_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResumeIdempotencyRecord(Base):
    """HITL resume 请求去重记录。

    做什么：
        在业务数据库中保存前端显式幂等键、处理租约及首次响应快照，避免重复调用
        LangGraph 恢复。该表不承担 Checkpoint 图状态职责。
    返回值：
        ORM 实体供 API 与 Repository 查询、占用和完成回写。
    """

    __tablename__ = "resume_idempotency_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_resume_idempotency_records_status",
        ),
        UniqueConstraint("thread_id", "idempotency_key", name="uq_resume_idempotency_thread_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_audit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResumeVersion(Base):
    """上传简历版本记录。

    做什么：
        保存简历资源身份、展示版本号、原始文件相对路径和索引生命周期状态；供
        上传服务、RAG 索引任务和简历库查询共同使用。
    返回值：
        ORM 实体本身由 Repository 返回，不包含文件读取或 Chroma 操作。
    """

    __tablename__ = "resume_versions"
    __table_args__ = (
        CheckConstraint("display_version > 0", name="ck_resume_versions_display_version"),
        CheckConstraint("file_size > 0", name="ck_resume_versions_file_size"),
        CheckConstraint(
            "index_status IN ('pending', 'indexing', 'indexed', 'failed')",
            name="ck_resume_versions_index_status",
        ),
        CheckConstraint(
            "(index_status = 'failed' AND error_code IS NOT NULL AND error_message IS NOT NULL) "
            "OR (index_status != 'failed' AND error_code IS NULL AND error_message IS NULL)",
            name="ck_resume_versions_error_state",
        ),
        UniqueConstraint("display_version", name="uq_resume_versions_display_version"),
    )

    resume_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    index_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResumeVersionCounter(Base):
    """简历展示版本的全局单行计数器。

    做什么：
        持久化已分配的最大 `display_version`，由 Repository 通过原子 SQL 递增。
    返回值：
        ORM 实体用于迁移和测试；业务代码不直接依赖其值生成版本号。
    """

    __tablename__ = "resume_version_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_display_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ResumeUploadIdempotencyRecord(Base):
    """简历上传请求去重记录。

    做什么：
        将 `Idempotency-Key` 与上传请求指纹和创建出的简历资源关联，避免网络重试
        或重复点击创建多份简历；与 HITL resume 去重表职责隔离。
    返回值：
        ORM 实体供上传 Repository 查询和重放首次受理结果。
    """

    __tablename__ = "resume_upload_idempotency_records"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_resume_upload_idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resume_id: Mapped[str] = mapped_column(ForeignKey("resume_versions.resume_id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)