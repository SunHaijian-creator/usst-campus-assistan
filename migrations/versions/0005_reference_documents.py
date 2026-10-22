"""新增用户提供的文档及定位片段，保留演示指南与全部会话。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_reference_documents"
down_revision = "0004_message_model"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("reference_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("provenance", sa.String(200), nullable=False),
        sa.Column("currency_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("warnings", JSONB(), nullable=False),
        sa.Column("document_meta", JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_key", "file_sha256", name="uq_reference_document_version"))
    op.create_index("ix_reference_documents_document_key", "reference_documents", ["document_key"])
    op.create_table("reference_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("reference_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_key", sa.String(100), nullable=False),
        sa.Column("heading", sa.String(255), nullable=False),
        sa.Column("locator", sa.String(255), nullable=False),
        sa.Column("pages", JSONB(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("usable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("table_rows", JSONB(), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_key", name="uq_reference_chunk_key"))
    op.create_index("ix_reference_chunks_document_id", "reference_chunks", ["document_id"])


def downgrade():
    op.drop_table("reference_chunks")
    op.drop_table("reference_documents")
