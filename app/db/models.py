"""业务 ORM 模型定义。

本模块声明 Week3 Task 7-9 需要的业务表，供 Repository、Alembic 与测试复用；
不包含查询逻辑或运行时副作用。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Text, UniqueConstraint
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