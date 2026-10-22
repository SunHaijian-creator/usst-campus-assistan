"""指南、用户、会话及消息的表映射；表结构通过 Alembic 创建。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey,
    Identity, Index, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Guide(Base):
    __tablename__ = "guides"
    __table_args__ = (UniqueConstraint("demo_key", name="uq_guides_demo_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(200), nullable=False)
    steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    materials: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # 导入脚本的稳定标识；允许未来正式指南不设置这个字段。
    demo_key: Mapped[str | None] = mapped_column(String(100), nullable=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )


class ReferenceDocument(Base):
    __tablename__ = "reference_documents"
    __table_args__ = (UniqueConstraint("document_key", "file_sha256", name="uq_reference_document_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    provenance: Mapped[str] = mapped_column(String(200), nullable=False)
    currency_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    document_meta: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReferenceChunk(Base):
    __tablename__ = "reference_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_key", name="uq_reference_chunk_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("reference_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_key: Mapped[str] = mapped_column(String(100), nullable=False)
    heading: Mapped[str] = mapped_column(String(255), nullable=False)
    locator: Mapped[str] = mapped_column(String(255), nullable=False)
    pages: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    usable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    table_rows: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", name="fk_conversations_user", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        CheckConstraint(
            "(role = 'user' AND char_length(content) BETWEEN 1 AND 4000) OR "
            "(role = 'assistant' AND char_length(content) BETWEEN 0 AND 16000)",
            name="ck_messages_content_length",
        ),
        CheckConstraint("status IN ('generating', 'completed', 'failed', 'cancelled')", name="ck_messages_status"),
        CheckConstraint("role = 'assistant' OR status = 'completed'", name="ck_messages_user_completed"),
        CheckConstraint("(status = 'generating') = (generation_expires_at IS NOT NULL)", name="ck_messages_generation_lease"),
        Index("uq_messages_active_generation", "conversation_id", unique=True, postgresql_where=text("status = 'generating'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", name="fk_messages_conversation", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="user")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="completed")
    reply_to_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", name="fk_messages_reply_to", ondelete="CASCADE"), nullable=True
    )
    references: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    generation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
