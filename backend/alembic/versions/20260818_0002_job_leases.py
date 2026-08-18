"""Add leases for cross-instance background job claims.

Revision ID: 20260818_0002
Revises: 20260817_0001
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_0002"
down_revision: str | None = "20260817_0001"
branch_labels: Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lease_owner", sa.String(length=120), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_jobs_lease_owner"), "jobs", ["lease_owner"], unique=False)
    op.create_index(op.f("ix_jobs_lease_expires_at"), "jobs", ["lease_expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_lease_expires_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_lease_owner"), table_name="jobs")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "lease_owner")
