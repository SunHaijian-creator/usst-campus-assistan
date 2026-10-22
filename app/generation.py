"""短事务内检查生成占用；没有跨网络生成期间持有的数据库锁。"""

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.models import Message


def recover_stale(session, conversation_id):
    session.execute(
        update(Message).where(
            Message.conversation_id == conversation_id,
            Message.status == "generating",
            Message.generation_expires_at <= func.clock_timestamp(),
        ).values(status="cancelled", generation_expires_at=None)
    )


def ensure_idle(session, conversation_id):
    recover_stale(session, conversation_id)
    active = session.scalar(select(Message.id).where(
        Message.conversation_id == conversation_id, Message.status == "generating"
    ).limit(1))
    if active is not None:
        raise HTTPException(status_code=409, detail="该会话正在生成回复，请等待完成或停止后重试")
