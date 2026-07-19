"""add uniqueness to experiment sampling keys

Revision ID: 20260720_0002
Revises: 20260719_0001
"""

from __future__ import annotations

from alembic import op


revision = "20260720_0002"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_runs") as batch_op:
        batch_op.create_unique_constraint(
            "uq_experiment_runs_sampling_key",
            ["case_name", "architecture", "model_name", "prompt_version", "run_index"],
        )


def downgrade() -> None:
    with op.batch_alter_table("experiment_runs") as batch_op:
        batch_op.drop_constraint("uq_experiment_runs_sampling_key", type_="unique")