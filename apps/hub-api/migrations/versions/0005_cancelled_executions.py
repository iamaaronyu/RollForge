"""Record execution leases revoked by user cancellation."""

from alembic import op

revision = "0005_cancelled_executions"
down_revision = "0004_fifo_scheduler"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("execution_status", "executions", type_="check")
    op.create_check_constraint(
        "execution_status",
        "executions",
        "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'EXPIRED', 'CANCELLED')",
    )


def downgrade():
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM executions WHERE status = 'CANCELLED') THEN
                RAISE EXCEPTION 'Cannot downgrade while cancelled executions exist';
            END IF;
        END $$;"""
    )
    op.drop_constraint("execution_status", "executions", type_="check")
    op.create_check_constraint(
        "execution_status",
        "executions",
        "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'EXPIRED')",
    )
