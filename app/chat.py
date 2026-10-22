"""聊天准备/结束各用短事务；SSE 期间不持有数据库会话或事务。"""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.config import get_settings
from app.answer_policy import ANSWER_POLICY_VERSION
from app.context_cache import get_context_cache
from app.conversations import owned_conversation
from app.database import SessionLocal, get_session
from app.generation import ensure_idle
from app.model_gateway import ModelFailure, ModelInput, ModelProvider, get_model_provider
from app.models import Conversation, Message
from app.retrieval import is_short_followup, retrieve_guides
from app.reference_search import domain_for, is_document_followup, search_documents
from app.reference_citations import validate_citations
from app.reference_loader import AID_WARNING
from app.schemas import ChatRequest, GuideReference
from app.security import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(tags=["流式聊天"])


@dataclass
class Turn:
    user_id: UUID
    conversation_id: UUID
    user_message_id: int
    assistant_message_id: int
    model_input: ModelInput
    references: list[dict]
    retrieval_source: str
    model_provider: str = "mock"
    model_name: str = "mock-campus-v1"
    parts: list[str] = field(default_factory=list)


def prepare_turn(session, conversation_id, user_id, data, provider=None):
    conversation = owned_conversation(session, conversation_id, user_id, lock=True)
    ensure_idle(session, conversation_id)
    context = get_context_cache().load(session, user_id, conversation_id)
    documents = search_documents(session, data.content)
    guides = [] if documents.domain else retrieve_guides(session, data.content)
    source = "current_question"
    if not documents.domain and not guides and context and (is_short_followup(data.content) or
            (domain_for(context[-1]["user"]) and is_document_followup(data.content))):
        previous = context[-1]["user"]
        if domain_for(previous):
            documents = search_documents(session, previous + " " + data.content)
        else:
            guides = retrieve_guides(session, previous)
        source = "last_completed_question" if guides or documents.references else "no_match"
    elif not guides and not documents.references:
        source = "no_match"
    references = ([ref.model_dump() for ref in documents.references] if documents.domain else
                  [GuideReference.model_validate(guide).model_dump() for guide in guides])
    user_message = Message(conversation_id=conversation_id, role="user", content=data.content)
    session.add(user_message)
    session.flush()
    assistant = Message(
        conversation_id=conversation_id, role="assistant", content="", status="generating",
        reply_to_id=user_message.id, references=references,
        model_provider=getattr(provider, "mode", "mock"),
        model_name=getattr(provider, "model_name", "mock-campus-v1"),
        generation_expires_at=datetime.now(timezone.utc) + timedelta(seconds=get_settings().model_timeout_seconds + 10),
    )
    session.add(assistant)
    conversation.updated_at = func.clock_timestamp()
    session.commit()
    return Turn(user_id, conversation_id, user_message.id, assistant.id,
                ModelInput(data.content, context, guides, data.mock_scenario, documents.references, documents.insufficient_reason), references, source,
                assistant.model_provider, assistant.model_name)


def finish_turn(turn, status):
    requested_status = status
    cache = get_context_cache()
    try:
        with SessionLocal.begin() as session:
            conversation = session.scalar(select(Conversation).where(
                Conversation.id == turn.conversation_id, Conversation.user_id == turn.user_id
            ).with_for_update())
            if conversation is None:
                return False
            message = session.get(Message, turn.assistant_message_id)
            if message is None or message.status != "generating":
                return False
            if message.generation_expires_at <= datetime.now(timezone.utc):
                status = "cancelled"
            message.status = status
            message.content = "".join(turn.parts)[:16000]
            # 检索候选不等于回答依据；只保存流中已经通过校验并实际输出的引用。
            used = set()
            for token in re.findall(r"[\[【]([^\]】]*)[\]】]", message.content):
                used.update(re.findall(r"R\d+", token))
                if token.strip().isdigit():
                    used.add(token.strip())
            turn.references = [ref for ref in turn.references if str(ref.get("citation_id", ref["id"])) in used]
            message.references = turn.references
            message.generation_expires_at = None
            conversation.updated_at = func.clock_timestamp()
            session.flush()
            if status == "completed":
                # 仍持有父行锁，删除操作不能在此缓存写入与提交之间插入。
                # 其他请求还会用 PostgreSQL 版本验证缓存；若提交失败，不能使用这份缓存。
                cache.rebuild(session, turn.user_id, turn.conversation_id)
        return status == requested_status
    except SQLAlchemyError:
        cache.delete(turn.user_id, turn.conversation_id)
        raise


def encode_sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def persist(turn, status):
    with anyio.CancelScope(shield=True):
        try:
            return await anyio.to_thread.run_sync(finish_turn, turn, status)
        except SQLAlchemyError as exc:
            logger.warning("生成结果保存失败（%s）", type(exc).__name__)
            return False


async def with_heartbeats(chunks):
    pending = asyncio.create_task(anext(chunks))
    try:
        while True:
            done, _ = await asyncio.wait([pending], timeout=5)
            if not done:
                yield None
                continue
            try:
                value = pending.result()
            except StopAsyncIteration:
                return
            yield value
            pending = asyncio.create_task(anext(chunks))
    finally:
        with anyio.CancelScope(shield=True):
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def checked_chunks(provider, data):
    allowed = {ref.citation_id for ref in data.documents} if data.documents else {str(guide.id) for guide in data.guides}
    used = set()
    text_parts = []
    async with aclosing(provider.stream(data)) as raw:
        async with aclosing(validate_citations(raw, allowed, reject_numeric=bool(allowed), used=used)) as checked:
            async for text in checked:
                text_parts.append(text)
                yield text
    # 仅对实际引用奖助文件的回答补齐版本提示，不强加来源或逐条拼接全部原文。
    if any(ref.citation_id in used and AID_WARNING in ref.warnings for ref in data.documents):
        if AID_WARNING not in "".join(text_parts):
            yield "\n\n" + AID_WARNING + "。"


async def events(turn, provider):
    finalized = False
    try:
        yield encode_sse("meta", {
            "conversation_id": str(turn.conversation_id), "user_message_id": turn.user_message_id,
            "assistant_message_id": turn.assistant_message_id, "mode": turn.model_provider,
            "model": turn.model_name, "simulated": turn.model_provider == "mock",
            "generation_method": "evidence_notice" if turn.model_provider == "local" else "model",
            "answer_policy_version": ANSWER_POLICY_VERSION,
            "references_status": "retrieved",
            "references": turn.references, "retrieval_source": turn.retrieval_source,
            "context_rounds": len(turn.model_input.turns),
        })
        try:
            async with asyncio.timeout(get_settings().model_timeout_seconds):
                async with aclosing(checked_chunks(provider, turn.model_input)) as chunks:
                    async with aclosing(with_heartbeats(chunks)) as ticks:
                        async for chunk in ticks:
                            if chunk is None:
                                yield ": heartbeat\n\n"
                                continue
                            if not isinstance(chunk, str) or not chunk:
                                continue
                            if sum(map(len, turn.parts)) + len(chunk) > 16000:
                                raise ModelFailure("回复超过本站保存长度限制，内容已截断，请缩短问题", code="output_truncated")
                            turn.parts.append(chunk)
                            yield encode_sse("delta", {"text": chunk})
                if not turn.parts:
                    raise ValueError("empty model output")
            if await persist(turn, "completed"):
                finalized = True
                yield encode_sse("done", {"assistant_message_id": turn.assistant_message_id, "status": "completed",
                                          "references": turn.references})
            else:
                yield encode_sse("error", {"code": "persistence_failed", "message": "未能确认回复已保存，请刷新历史后重试"})
        except TimeoutError:
            finalized = await persist(turn, "failed")
            yield encode_sse("error", {"code": "timeout", "message": "回复生成超时，请稍后手动重试"})
        except ModelFailure as exc:
            finalized = await persist(turn, "failed")
            yield encode_sse("error", {"code": exc.code, "message": exc.safe_message})
        except Exception as exc:
            logger.warning("回复生成失败（%s）", type(exc).__name__)
            finalized = await persist(turn, "failed")
            yield encode_sse("error", {"code": "generation_failed", "message": "回复生成失败，请稍后重试"})
    finally:
        if not finalized:
            await persist(turn, "cancelled")


class ChatResponse(StreamingResponse):
    """所有 ASGI 版本都监听断开，确保暂停在 yield 处的生成器也被关闭。"""

    def __init__(self, turn, provider):
        self.turn = turn
        super().__init__(events(turn, provider), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        })

    async def __call__(self, scope, receive, send):
        try:
            async with anyio.create_task_group() as group:
                async def disconnected():
                    while True:
                        if (await receive())["type"] == "http.disconnect":
                            group.cancel_scope.cancel()
                            return
                group.start_soon(disconnected)
                try:
                    await self.stream_response(send)
                except OSError:
                    pass
                finally:
                    group.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
                await persist(self.turn, "cancelled")


@router.get("/chat/config", summary="查看运行模式和模型（不返回密钥）")
def chat_config():
    settings = get_settings()
    from app.deepseek import configuration_error
    simulated = settings.model_provider == "mock"
    reason = None if simulated else configuration_error(settings)
    return {"mode": settings.model_provider, "model": "mock-campus-v1" if simulated else settings.model_name,
            "simulated": simulated, "ready": reason is None, "configuration_error": reason,
            "max_message_chars": 4000, "answer_policy_version": ANSWER_POLICY_VERSION}


@router.post("/conversations/{conversation_id}/chat", summary="发送问题并读取 SSE 回复", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
                        409: {"description": "同一会话正在生成"}})
def chat(
    conversation_id: UUID, data: ChatRequest, user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    provider: Annotated[ModelProvider, Depends(get_model_provider)],
):
    if getattr(provider, "mode", "mock") != "mock" and data.mock_scenario != "normal":
        raise HTTPException(status_code=422, detail="模拟失败和超时场景只适用于 mock 模式")
    turn = prepare_turn(session, conversation_id, user.id, data, provider)
    # 认证依赖也使用此会话；开始 SSE 前明确归还连接。
    session.close()
    return ChatResponse(turn, provider)
