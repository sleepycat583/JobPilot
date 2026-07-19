"""create business tables

Revision ID: 20260719_0001
Revises: None
Create Date: 2026-07-19 15:48:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260719_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_name", sa.String(length=200), nullable=False),
        sa.Column("architecture", sa.String(length=100), nullable=False),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("schema_valid", sa.Boolean(), nullable=False),
        sa.Column("unsupported_skill_claims", sa.Integer(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error_codes", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('success', 'degraded', 'failed')", name="ck_experiment_runs_status"),
    )
    op.create_index("ix_experiment_runs_architecture", "experiment_runs", ["architecture"], unique=False)
    op.create_index("ix_experiment_runs_case_name", "experiment_runs", ["case_name"], unique=False)

    op.create_table(
        "review_audits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("review_target", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("command_json", sa.Text(), nullable=False),
        sa.Column("checkpoint_status_before", sa.String(length=30), nullable=True),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("result_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_audits_session_id", "review_audits", ["session_id"], unique=False)
    op.create_index("ix_review_audits_thread_id", "review_audits", ["thread_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_review_audits_thread_id", table_name="review_audits")
    op.drop_index("ix_review_audits_session_id", table_name="review_audits")
    op.drop_table("review_audits")

    op.drop_index("ix_experiment_runs_case_name", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_architecture", table_name="experiment_runs")
    op.drop_table("experiment_runs")