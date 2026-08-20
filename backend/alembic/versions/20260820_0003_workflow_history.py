"""Persist match reports and completed interview reviews.

Revision ID: 20260820_0003
Revises: 20260818_0002
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0003"
down_revision: str | None = "20260818_0002"
branch_labels: Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "match_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("jd_id", sa.String(length=36), nullable=False),
        sa.Column("strict", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_match_reports_thread_id"), "match_reports", ["thread_id"], unique=False)
    op.create_index(op.f("ix_match_reports_resume_id"), "match_reports", ["resume_id"], unique=False)
    op.create_index(op.f("ix_match_reports_jd_id"), "match_reports", ["jd_id"], unique=False)
    op.create_index("ix_match_reports_thread_created", "match_reports", ["thread_id", "created_at"], unique=False)
    op.create_table(
        "interview_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("jd_id", sa.String(length=36), nullable=False),
        sa.Column("interview_type", sa.String(length=32), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_interview_records_thread_id"), "interview_records", ["thread_id"], unique=False)
    op.create_index(op.f("ix_interview_records_resume_id"), "interview_records", ["resume_id"], unique=False)
    op.create_index(op.f("ix_interview_records_jd_id"), "interview_records", ["jd_id"], unique=False)
    op.create_index("ix_interview_records_thread_created", "interview_records", ["thread_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_interview_records_thread_created", table_name="interview_records")
    op.drop_index(op.f("ix_interview_records_jd_id"), table_name="interview_records")
    op.drop_index(op.f("ix_interview_records_resume_id"), table_name="interview_records")
    op.drop_index(op.f("ix_interview_records_thread_id"), table_name="interview_records")
    op.drop_table("interview_records")
    op.drop_index("ix_match_reports_thread_created", table_name="match_reports")
    op.drop_index(op.f("ix_match_reports_jd_id"), table_name="match_reports")
    op.drop_index(op.f("ix_match_reports_resume_id"), table_name="match_reports")
    op.drop_index(op.f("ix_match_reports_thread_id"), table_name="match_reports")
    op.drop_table("match_reports")
