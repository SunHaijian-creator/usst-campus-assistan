"""新增用户、会话及消息；保留指南表和数据。"""

from alembic import op
import sqlalchemy as sa

revision = "0002_auth_history"
down_revision = "0001_create_guides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_conversations_user", ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at", "id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), server_default="user", nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="fk_messages_conversation", ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.CheckConstraint("char_length(content) BETWEEN 1 AND 4000", name="ck_messages_content_length"),
    )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
