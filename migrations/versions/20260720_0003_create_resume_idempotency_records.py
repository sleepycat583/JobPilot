"""create resume idempotency records

Revision ID: 20260720_0003
Revises: 20260720_0002
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260720_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建与 LangGraph Checkpoint 物理隔离的 resume 去重业务表。"""

    op.create_table(
        "resume_idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("review_audit_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_resume_idempotency_records_status",
        ),
    )
    op.create_index("ix_resume_idempotency_records_thread_id", "resume_idempotency_records", ["thread_id"], unique=False)
    op.create_index("ix_resume_idempotency_records_session_id", "resume_idempotency_records", ["session_id"], unique=False)
    # SQLite 通过 batch mode 重建表以可靠添加唯一约束。
    with op.batch_alter_table("resume_idempotency_records") as batch_op:
        batch_op.create_unique_constraint("uq_resume_idempotency_thread_key", ["thread_id", "idempotency_key"])


def downgrade() -> None:
    """删除 resume 去重记录表。"""

    op.drop_index("ix_resume_idempotency_records_session_id", table_name="resume_idempotency_records")
    op.drop_index("ix_resume_idempotency_records_thread_id", table_name="resume_idempotency_records")
    op.drop_table("resume_idempotency_records")