"""只缓存完整成功的问答；每次用 PostgreSQL 的最新完成消息 ID 校验版本。"""

from functools import lru_cache
import json
import logging

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.cache import create_redis_client
from app.answer_policy import ANSWER_POLICY_VERSION
from app.config import get_settings
from app.models import Message

logger = logging.getLogger(__name__)


class ContextCache:
    def __init__(self, client, prefix):
        self.client = client
        self.prefix = prefix

    def key(self, user_id, conversation_id):
        return f"{self.prefix}:{user_id}:{conversation_id}"

    def delete(self, user_id, conversation_id):
        try:
            self.client.delete(self.key(user_id, conversation_id))
        except RedisError as exc:
            logger.warning("上下文缓存删除失败，残留键将按 TTL 到期（%s）", type(exc).__name__)

    def rebuild(self, session, user_id, conversation_id, *, write=True):
        settings = get_settings()
        user_message = aliased(Message)
        pairs = session.execute(
            select(Message, user_message)
            .join(user_message, Message.reply_to_id == user_message.id)
            .where(
                Message.conversation_id == conversation_id, Message.role == "assistant",
                Message.status == "completed", user_message.role == "user",
                user_message.status == "completed", user_message.conversation_id == conversation_id,
            ).order_by(Message.id.desc()).limit(settings.chat_context_rounds)
        ).all()
        remaining = settings.chat_context_max_chars
        turns = []
        for assistant, user in pairs:
            if remaining < 2:
                break
            user_text, assistant_text = user.content, assistant.content
            if len(user_text) + len(assistant_text) > remaining:
                user_text = user_text[:min(len(user_text), max(1, remaining // 3))]
                assistant_text = assistant_text[:remaining - len(user_text)]
            remaining -= len(user_text) + len(assistant_text)
            turns.append({
                "user_message_id": user.id, "assistant_message_id": assistant.id,
                "user": user_text, "assistant": assistant_text,
            })
        payload = {
            "answer_policy_version": ANSWER_POLICY_VERSION,
            "user_id": str(user_id), "conversation_id": str(conversation_id),
            "version": pairs[0][0].id if pairs else 0,
            "limits": [settings.chat_context_rounds, settings.chat_context_max_chars],
            "turns": list(reversed(turns)),
        }
        if write:
            try:
                self.client.set(
                    self.key(user_id, conversation_id), json.dumps(payload, ensure_ascii=False),
                    ex=settings.chat_context_ttl,
                )
            except RedisError as exc:
                logger.warning("上下文缓存写入失败，继续使用 PostgreSQL（%s）", type(exc).__name__)
        return payload["turns"]

    def load(self, session, user_id, conversation_id):
        settings = get_settings()
        version = session.scalar(select(func.max(Message.id)).where(
            Message.conversation_id == conversation_id, Message.role == "assistant",
            Message.status == "completed", Message.reply_to_id.is_not(None),
        )) or 0
        try:
            raw = self.client.get(self.key(user_id, conversation_id))
            if raw:
                payload = json.loads(raw)
                turns = payload["turns"]
                if (
                    payload.get("answer_policy_version") == ANSWER_POLICY_VERSION
                    and payload["user_id"] == str(user_id)
                    and payload["conversation_id"] == str(conversation_id)
                    and payload["version"] == version
                    and payload["limits"] == [settings.chat_context_rounds, settings.chat_context_max_chars]
                    and isinstance(turns, list) and len(turns) <= settings.chat_context_rounds
                    and all(
                        isinstance(t, dict) and isinstance(t["user"], str) and isinstance(t["assistant"], str)
                        and isinstance(t["user_message_id"], int) and isinstance(t["assistant_message_id"], int)
                        for t in turns
                    )
                    and sum(len(t["user"]) + len(t["assistant"]) for t in turns) <= settings.chat_context_max_chars
                ):
                    return turns
        except (RedisError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            logger.warning("上下文缓存未命中或不可用，从 PostgreSQL 恢复（%s）", type(exc).__name__)
        return self.rebuild(session, user_id, conversation_id)


@lru_cache
def get_context_cache():
    return ContextCache(create_redis_client(), get_settings().chat_context_prefix)
