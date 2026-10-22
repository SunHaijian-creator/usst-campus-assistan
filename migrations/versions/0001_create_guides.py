"""创建办事指南表，不修改已有的其他表或数据卷。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_create_guides"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("department", sa.String(200), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("materials", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("demo_key", sa.String(100), nullable=True),
        sa.UniqueConstraint("demo_key", name="uq_guides_demo_key"),
    )
    op.create_index("ix_guides_category", "guides", ["category"])


def downgrade() -> None:
    op.drop_index("ix_guides_category", table_name="guides")
    op.drop_table("guides")
