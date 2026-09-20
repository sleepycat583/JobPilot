"""Persist employer due diligence reports."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260915_0004"
down_revision: str | None = "20260820_0003"
branch_labels: Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "employer_investigations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("subject_name", sa.String(length=255), nullable=False),
        sa.Column("unified_social_credit_code", sa.String(length=32), nullable=False),
        sa.Column("result_status", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("provider_request_id", sa.String(length=120), nullable=True),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_employer_investigations_thread_id"), "employer_investigations", ["thread_id"], unique=False)
    op.create_index(op.f("ix_employer_investigations_unified_social_credit_code"), "employer_investigations", ["unified_social_credit_code"], unique=False)
    op.create_index(op.f("ix_employer_investigations_result_status"), "employer_investigations", ["result_status"], unique=False)
    op.create_index("ix_employer_investigations_thread_created", "employer_investigations", ["thread_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_employer_investigations_thread_created", table_name="employer_investigations")
    op.drop_index(op.f("ix_employer_investigations_result_status"), table_name="employer_investigations")
    op.drop_index(op.f("ix_employer_investigations_unified_social_credit_code"), table_name="employer_investigations")
    op.drop_index(op.f("ix_employer_investigations_thread_id"), table_name="employer_investigations")
    op.drop_table("employer_investigations")
