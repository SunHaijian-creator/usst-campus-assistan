"""所有历史操作先校验归属；写入和删除通过同一事务及父记录行锁串行处理。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Conversation, Message
from app.schemas import (
    ConversationCreate, ConversationPage, ConversationRead,
    MessageCreate, MessagePage, MessageRead,
)
from app.security import CurrentUser
from app.generation import ensure_idle, recover_stale
from app.context_cache import get_context_cache

router = APIRouter(prefix="/conversations", tags=["会话与消息"])
SessionDep = Annotated[Session, Depends(get_session)]
Page = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def owned_conversation(session: Session, conversation_id: UUID, user_id: UUID, *, lock=False) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update()
    conversation = session.scalar(statement)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.post("", response_model=ConversationRead, status_code=201, summary="创建自己的会话")
def create_conversation(data: ConversationCreate, user: CurrentUser, session: SessionDep) -> Conversation:
    conversation = Conversation(user_id=user.id, title=data.title)
    session.add(conversation)
    session.commit()
    return conversation


@router.get("", response_model=ConversationPage, summary="按更新时间倒序查询自己的会话")
def list_conversations(
    user: CurrentUser, session: SessionDep, page: Page = 1, page_size: PageSize = 20,
) -> ConversationPage:
    filters = [Conversation.user_id == user.id]
    total = session.scalar(select(func.count()).select_from(Conversation).where(*filters)) or 0
    conversations = session.scalars(
        select(Conversation).where(*filters)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return ConversationPage(
        items=[ConversationRead.model_validate(item) for item in conversations],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{conversation_id}", response_model=ConversationRead, summary="查看自己的会话")
def read_conversation(conversation_id: UUID, user: CurrentUser, session: SessionDep) -> Conversation:
    return owned_conversation(session, conversation_id, user.id)


@router.delete("/{conversation_id}", status_code=204, summary="删除自己的会话及关联消息")
def delete_conversation(conversation_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    conversation = owned_conversation(session, conversation_id, user.id, lock=True)
    ensure_idle(session, conversation_id)
    get_context_cache().delete(user.id, conversation_id)
    session.delete(conversation)
    # 外键 ON DELETE CASCADE 在本次事务内删除关联消息。
    session.commit()
    return Response(status_code=204)


@router.post(
    "/{conversation_id}/messages", response_model=MessageRead,
    status_code=201, summary="保存一条用户消息",
)
def create_message(
    conversation_id: UUID, data: MessageCreate, user: CurrentUser, session: SessionDep,
) -> Message:
    conversation = owned_conversation(session, conversation_id, user.id, lock=True)
    ensure_idle(session, conversation_id)
    message = Message(conversation_id=conversation.id, role="user", content=data.content)
    session.add(message)
    session.flush()
    conversation.updated_at = func.clock_timestamp()
    session.commit()
    return message


@router.get("/{conversation_id}/messages", response_model=MessagePage, summary="按写入顺序读取历史消息")
def list_messages(
    conversation_id: UUID, user: CurrentUser, session: SessionDep,
    page: Page = 1, page_size: PageSize = 20,
) -> MessagePage:
    owned_conversation(session, conversation_id, user.id, lock=True)
    recover_stale(session, conversation_id)
    session.commit()
    filters = [Message.conversation_id == conversation_id]
    total = session.scalar(select(func.count()).select_from(Message).where(*filters)) or 0
    messages = session.scalars(
        select(Message).where(*filters).order_by(Message.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return MessagePage(
        items=[MessageRead.model_validate(item) for item in messages],
        total=total, page=page, page_size=page_size,
    )
