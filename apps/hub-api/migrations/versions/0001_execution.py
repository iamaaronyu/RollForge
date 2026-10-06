"""S1 单 Trial 与有界 Execution 租约。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_execution"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_owner_id", "jobs", ["owner_id"])
    op.create_table(
        "trials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                native_enum=False,
                create_constraint=True,
                name="trial_status",
            ),
            nullable=False,
        ),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.UniqueConstraint("job_id", name="uq_trials_single_job"),
        sa.CheckConstraint("attempt_index = 0", name="ck_trials_attempt"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_trials_token"),
    )
    op.create_index("ix_trials_queue", "trials", ["status", "id"])
    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trial_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "EXPIRED",
                native_enum=False,
                create_constraint=True,
                name="execution_status",
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["trial_id"], ["trials.id"]),
        sa.UniqueConstraint("trial_id", "fencing_token", name="uq_execution_token"),
        sa.CheckConstraint("fencing_token > 0", name="ck_execution_token"),
    )
    op.create_index("ix_execution_expiry", "executions", ["status", "expires_at"])


def downgrade():
    op.drop_table("executions")
    op.drop_table("trials")
    op.drop_table("jobs")
