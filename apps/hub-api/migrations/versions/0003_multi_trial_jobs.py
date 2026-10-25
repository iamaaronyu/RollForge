"""确定性展开一个 Job 的多条逻辑 Trial；Retry 仍使用新 Execution。"""

import sqlalchemy as sa
from alembic import op

revision = "0003_multi_trial"
down_revision = "0002_registry"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs", sa.Column("trial_count", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "jobs", sa.Column("execution_sequence", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint("ck_jobs_trial_count", "jobs", "trial_count BETWEEN 1 AND 1000")
    op.create_check_constraint("ck_jobs_execution_sequence", "jobs", "execution_sequence >= 0")
    op.alter_column("jobs", "trial_count", server_default=None)
    op.add_column("executions", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.add_column("executions", sa.Column("job_sequence", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE executions AS e SET job_id = t.job_id, job_sequence = e.fencing_token
        FROM trials AS t WHERE t.id = e.trial_id
    """)
    op.alter_column("executions", "job_id", nullable=False)
    op.alter_column("executions", "job_sequence", nullable=False)
    op.create_foreign_key("fk_executions_job_id_jobs", "executions", "jobs", ["job_id"], ["id"])
    op.create_unique_constraint(
        "uq_execution_job_sequence", "executions", ["job_id", "job_sequence"]
    )
    op.create_check_constraint("ck_execution_job_sequence", "executions", "job_sequence >= 1")
    op.execute("""
        UPDATE jobs AS j SET execution_sequence = COALESCE(
            (SELECT max(e.job_sequence) FROM executions AS e WHERE e.job_id = j.id), 0
        )
    """)
    op.alter_column("jobs", "execution_sequence", server_default=None)
    op.drop_constraint("uq_trials_single_job", "trials", type_="unique")
    op.drop_constraint("ck_trials_attempt", "trials", type_="check")
    op.create_unique_constraint("uq_trials_job_attempt", "trials", ["job_id", "attempt_index"])
    op.create_check_constraint("ck_trials_attempt", "trials", "attempt_index >= 0")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM jobs WHERE trial_count <> 1)
               OR EXISTS (SELECT 1 FROM trials WHERE attempt_index <> 0) THEN
                RAISE EXCEPTION 'Cannot downgrade while multi-trial jobs exist';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_trials_attempt", "trials", type_="check")
    op.drop_constraint("uq_trials_job_attempt", "trials", type_="unique")
    op.create_unique_constraint("uq_trials_single_job", "trials", ["job_id"])
    op.create_check_constraint("ck_trials_attempt", "trials", "attempt_index = 0")
    op.drop_constraint("ck_jobs_trial_count", "jobs", type_="check")
    op.drop_constraint("ck_jobs_execution_sequence", "jobs", type_="check")
    op.drop_constraint("ck_execution_job_sequence", "executions", type_="check")
    op.drop_constraint("uq_execution_job_sequence", "executions", type_="unique")
    op.drop_constraint("fk_executions_job_id_jobs", "executions", type_="foreignkey")
    op.drop_column("executions", "job_sequence")
    op.drop_column("executions", "job_id")
    op.drop_column("jobs", "execution_sequence")
    op.drop_column("jobs", "trial_count")
