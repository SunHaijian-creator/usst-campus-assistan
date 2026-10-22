"""新增消息生成状态、问答关联、参考资料及有期限的单会话生成占用。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_streaming_chat"
down_revision = "0002_auth_history"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("status", sa.String(16), nullable=False, server_default="completed"))
    op.add_column("messages", sa.Column("reply_to_id", sa.BigInteger(), nullable=True))
    op.add_column("messages", sa.Column("references", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("messages", sa.Column("generation_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_messages_reply_to", "messages", "messages", ["reply_to_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint("ck_messages_content_length", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_content_length", "messages",
        "(role = 'user' AND char_length(content) BETWEEN 1 AND 4000) OR "
        "(role = 'assistant' AND char_length(content) BETWEEN 0 AND 16000)",
    )
    op.create_check_constraint("ck_messages_status", "messages", "status IN ('generating', 'completed', 'failed', 'cancelled')")
    op.create_check_constraint("ck_messages_user_completed", "messages", "role = 'assistant' OR status = 'completed'")
    op.create_check_constraint("ck_messages_generation_lease", "messages", "(status = 'generating') = (generation_expires_at IS NOT NULL)")
    op.create_index("uq_messages_active_generation", "messages", ["conversation_id"], unique=True, postgresql_where=sa.text("status = 'generating'"))


def downgrade():
    # 存在新阶段的空/长助手消息时，旧版约束可能无法容纳；不悄悄删除历史。
    op.drop_index("uq_messages_active_generation", table_name="messages")
    op.drop_constraint("ck_messages_user_completed", "messages", type_="check")
    op.drop_constraint("ck_messages_generation_lease", "messages", type_="check")
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.drop_constraint("ck_messages_content_length", "messages", type_="check")
    op.create_check_constraint("ck_messages_content_length", "messages", "char_length(content) BETWEEN 1 AND 4000")
    op.drop_constraint("fk_messages_reply_to", "messages", type_="foreignkey")
    for name in ("generation_expires_at", "references", "reply_to_id", "status"):
        op.drop_column("messages", name)
