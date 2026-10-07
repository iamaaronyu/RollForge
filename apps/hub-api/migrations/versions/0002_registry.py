"""不可变注册表：资产所有权/类型固定，版本只追加。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_registry"
down_revision = "0001_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "registry_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.CheckConstraint("kind IN ('TASK', 'AGENT', 'MODEL')", name="ck_registry_kind"),
    )
    op.create_index("ix_registry_assets_owner_id", "registry_assets", ["owner_id"])
    op.create_table(
        "registry_revisions",
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("registry_assets.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("revision >= 1 AND revision <= 1000000", name="ck_registry_revision"),
    )
    op.execute("""
        CREATE FUNCTION reject_registry_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Registry records are immutable' USING ERRCODE = '23514';
        END;
        $$
    """)
    for table in ("registry_assets", "registry_revisions"):
        op.execute(f"""
            CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_registry_mutation()
        """)
        op.execute(f"""
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_registry_mutation()
        """)


def downgrade():
    op.drop_table("registry_revisions")
    op.drop_table("registry_assets")
    op.execute("DROP FUNCTION reject_registry_mutation()")
