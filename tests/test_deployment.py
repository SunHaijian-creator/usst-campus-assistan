"""部署新增行为：就绪检查降级，以及初始化重复执行保留既有数据。"""

from uuid import UUID

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.context_cache import get_context_cache
from app.database import SessionLocal
from app.models import Message, Guide, ReferenceDocument, ReferenceChunk
from scripts.initialize_database import initialize_database
from scripts.setup_env import setup_environment
from app.config import PROJECT_ROOT
from test_chat import chat_account

pytestmark = pytest.mark.anyio


async def test_first_setup_generates_private_configuration_and_preserves_existing(tmp_path):
    (tmp_path / ".env.example").write_bytes((PROJECT_ROOT / ".env.example").read_bytes())
    assert setup_environment(tmp_path) is True
    generated = (tmp_path / ".env").read_bytes()
    assert b"replace_with_your_local_password" not in generated
    assert b"JWT_SECRET_KEY=\n" not in generated and b"MODEL_API_KEY=\n" in generated
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    assert setup_environment(tmp_path) is False
    assert (tmp_path / ".env").read_bytes() == generated


async def test_readiness_database_required_redis_optional(api, monkeypatch):
    assert (await api.get("/ready")).json() == {"status": "ready", "database": "ok", "redis": "ok"}
    def offline():
        raise RedisConnectionError("offline test")
    monkeypatch.setattr(get_context_cache().client, "ping", offline)
    degraded = await api.get("/ready")
    assert degraded.status_code == 200 and degraded.json()["redis"] == "unavailable"
    import app.main as main_module
    class DatabaseOffline:
        def connect(self):
            raise OperationalError("offline", {}, None)
    monkeypatch.setattr(main_module, "engine", DatabaseOffline())
    response = await api.get("/ready")
    assert response.status_code == 503 and response.json() == {"status": "not_ready", "database": "unavailable"}
    assert (await api.get("/health")).json() == {"status": "ok"}


async def test_repeat_initialization_preserves_existing_history_and_references(api, chat_account):
    result = await api.post(chat_account["url"] + "/messages", headers=chat_account["headers"], json={"content": "迁移与导入不能覆盖已有历史"})
    assert result.status_code == 201
    first = initialize_database()
    assert first["migration"] == "head"
    def snapshot():
        with SessionLocal() as session:
            return {
                "guides": [(g.id, g.updated_at) for g in session.scalars(select(Guide).order_by(Guide.id))],
                "documents": [(d.id, d.file_sha256, d.created_at) for d in session.scalars(select(ReferenceDocument).order_by(ReferenceDocument.id))],
                "chunks": [(c.id, c.content_sha256) for c in session.scalars(select(ReferenceChunk).order_by(ReferenceChunk.id))],
                "messages": [(m.id, m.content, m.status) for m in session.scalars(select(Message).where(Message.conversation_id == UUID(chat_account["id"])))],
            }
    before = snapshot()
    for _ in range(2):
        repeated = initialize_database()
        assert repeated["demo_guides_added"] == repeated["documents_added"] == repeated["chunks_added"] == 0
    assert snapshot() == before
    history = (await api.get(chat_account["url"] + "/messages", headers=chat_account["headers"])).json()["items"]
    assert history == [result.json()]
