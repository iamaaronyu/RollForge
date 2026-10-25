"""为调度队列提供稳定 FIFO 顺序和可查询索引。"""

import sqlalchemy as sa
from alembic import op

revision = "0004_fifo_scheduler"
down_revision = "0003_multi_trial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "trials",
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.drop_index("ix_trials_queue", table_name="trials")
    op.create_index("ix_trials_queue", "trials", ["status", "queued_at", "job_id", "attempt_index"])


def downgrade():
    op.drop_index("ix_trials_queue", table_name="trials")
    op.create_index("ix_trials_queue", "trials", ["status", "id"])
    op.drop_column("trials", "queued_at")
