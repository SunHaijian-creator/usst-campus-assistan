"""API 的响应结构；同时用于验证缓存中的 JSON。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class GuideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: str
    department: str
    steps: list[str]
    materials: list[str]
    source: str
    updated_at: datetime
    is_demo: bool


class GuidePage(BaseModel):
    items: list[GuideRead]
    total: int
    page: int
    page_size: int


class ErrorResponse(BaseModel):
    detail: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_]+$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="新会话", min_length=1, max_length=100)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationRead]
    total: int
    page: int
    page_size: int


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息内容不能为空白")
        return value


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    status: Literal["generating", "completed", "failed", "cancelled"]
    references: list["SourceReference"]
    model_provider: str | None
    model_name: str | None


class MessagePage(BaseModel):
    items: list[MessageRead]
    total: int
    page: int
    page_size: int


class GuideReference(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source: str
    is_demo: bool


class DocumentReference(BaseModel):
    kind: Literal["document"] = "document"
    citation_id: str = Field(pattern=r"^R[1-9][0-9]*$")
    id: int
    document_id: int
    title: str
    original_filename: str
    file_sha256: str
    version: str
    source: str
    is_demo: Literal[False] = False
    currency_verified: Literal[False] = False
    locator: str
    pages: list[int]
    excerpt: str
    warnings: list[str]
    table_rows: list[dict]
    original_url: str


SourceReference = DocumentReference | GuideReference


class ChatRequest(MessageCreate):
    mock_scenario: Literal["normal", "failure", "timeout"] = "normal"
