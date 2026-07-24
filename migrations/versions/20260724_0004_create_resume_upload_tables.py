"""create resume upload tables

Revision ID: 20260724_0004
Revises: 20260720_0003
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260724_0004"
down_revision = "20260720_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建简历版本、展示版本计数器和上传幂等业务表。"""

    op.create_table(
        "resume_versions",
        sa.Column("resume_id", sa.String(length=36), primary_key=True),
        sa.Column("display_version", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("index_status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("display_version > 0", name="ck_resume_versions_display_version"),
        sa.CheckConstraint("file_size > 0", name="ck_resume_versions_file_size"),
        sa.CheckConstraint(
            "index_status IN ('pending', 'indexing', 'indexed', 'failed')",
            name="ck_resume_versions_index_status",
        ),
        sa.CheckConstraint(
            "(index_status = 'failed' AND error_code IS NOT NULL AND error_message IS NOT NULL) "
            "OR (index_status != 'failed' AND error_code IS NULL AND error_message IS NULL)",
            name="ck_resume_versions_error_state",
        ),
        sa.UniqueConstraint("display_version", name="uq_resume_versions_display_version"),
    )
    op.create_table(
        "resume_version_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_display_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "resume_upload_idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resume_versions.resume_id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_resume_upload_idempotency_key"),
    )
    op.create_index(
        "ix_resume_upload_idempotency_records_expires_at",
        "resume_upload_idempotency_records",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除简历上传业务表。"""

    op.drop_index("ix_resume_upload_idempotency_records_expires_at", table_name="resume_upload_idempotency_records")
    op.drop_table("resume_upload_idempotency_records")
    op.drop_table("resume_version_counters")
    op.drop_table("resume_versions")