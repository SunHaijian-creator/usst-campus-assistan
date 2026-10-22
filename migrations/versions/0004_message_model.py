"""保存助手消息的供应商和模型标识，保留已有消息及指南。"""

from alembic import op
import sqlalchemy as sa

revision = "0004_message_model"
down_revision = "0003_streaming_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("model_provider", sa.String(16), nullable=True))
    op.add_column("messages", sa.Column("model_name", sa.String(100), nullable=True))
    # 第四阶段生成的所有助手消息都来自固定模板；不能随配置切换冒充真实回复。
    op.execute("UPDATE messages SET model_provider='mock', model_name='mock-campus-v1' WHERE role='assistant'")


def downgrade():
    op.drop_column("messages", "model_name")
    op.drop_column("messages", "model_provider")
