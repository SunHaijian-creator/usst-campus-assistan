"""使用独立 PostgreSQL、明确 Redis 键和可控模型验证聊天状态机。"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.chat import finish_turn, prepare_turn
from app.config import get_settings
from app.context_cache import ContextCache, get_context_cache
from app.database import SessionLocal, engine
from app.main import app
from app.model_gateway import get_model_provider
from app.models import Conversation, Message
from app.schemas import ChatRequest

pytestmark = pytest.mark.anyio


@pytest.fixture
async def chat_account(api):
    username = f"chat_{uuid4().hex[:20]}"
    password = "Chat-test-password-2026"
    user = await api.post("/auth/register", json={"username": username, "password": password})
    assert user.status_code == 201
    token = await api.post("/auth/token", data={"username": username, "password": password})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    conversation = await api.post("/conversations", headers=headers, json={})
    cid = conversation.json()["id"]
    account = {"headers": headers, "user_id": user.json()["id"], "id": cid, "url": f"/conversations/{cid}"}
    try:
        yield account
    finally:
        get_context_cache().delete(UUID(account["user_id"]), UUID(cid))


def frames(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert "no-store" in response.headers["cache-control"]
    result = []
    for block in response.text.split("\n\n"):
        if not block or block.startswith(":"):
            continue
        lines = block.splitlines()
        result.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return result


async def send(api, account, content="校园卡补办需要什么材料？", **extra):
    return frames(await api.post(account["url"] + "/chat", headers=account["headers"], json={"content": content, **extra}))


async def test_normal_stream_references_history_and_multiturn(api, chat_account):
    first = await send(api, chat_account)
    assert first[0][0] == "meta" and first[-1][0] == "done"
    assert sum(name == "delta" for name, data in first) > 1
    meta = first[0][1]
    assert meta["simulated"] and meta["mode"] == "mock" and meta["context_rounds"] == 0
    assert meta["references"][0]["title"] == "校园卡补办"
    assert meta["references"][0]["is_demo"] and "演示" in meta["references"][0]["source"]
    answer = "".join(data["text"] for name, data in first if name == "delta")
    assert "模拟回复" in answer and "有效身份证件" in answer
    history = (await api.get(chat_account["url"] + "/messages", headers=chat_account["headers"])).json()["items"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert all(item["status"] == "completed" for item in history)
    assert history[-1]["content"] == answer and history[-1]["references"] == meta["references"]
    followup = await send(api, chat_account, "还需要什么材料？")
    assert followup[0][1]["context_rounds"] == 1
    assert followup[0][1]["retrieval_source"] == "last_completed_question"
    assert followup[0][1]["references"] == meta["references"]


async def test_no_guides_means_unable_to_confirm(api, chat_account):
    await send(api, chat_account)  # 不能因为上一轮有指南，就给无关的新问题引用旧指南。
    result = await send(api, chat_account, "火星外星天气预报")
    assert result[0][1]["references"] == []
    answer = "".join(data["text"] for event, data in result if event == "delta")
    assert "无法确认" in answer and "模拟" in answer


async def test_expired_worker_cannot_report_completed(api, chat_account):
    with SessionLocal() as session:
        turn = prepare_turn(session, UUID(chat_account["id"]), UUID(chat_account["user_id"]), ChatRequest(content="校园卡补办"))
    with SessionLocal.begin() as session:
        session.get(Message, turn.assistant_message_id).generation_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    turn.parts = ["【模拟回复】迟到的结果"]
    assert finish_turn(turn, "completed") is False
    with SessionLocal() as session:
        assert session.get(Message, turn.assistant_message_id).status == "cancelled"
    assert (await send(api, chat_account))[0][1]["context_rounds"] == 0


@pytest.mark.parametrize("scenario", ["failure", "timeout"])
async def test_provider_resources_close_after_error(api, chat_account, monkeypatch, scenario):
    import asyncio
    monkeypatch.setattr(get_settings(), "model_timeout_seconds", 0.1)
    closed = []
    class ResourceProvider:
        async def stream(self, data):
            try:
                yield "【模拟回复】"
                if scenario == "failure":
                    raise RuntimeError("private provider detail")
                await asyncio.sleep(10)
            finally:
                closed.append(True)
    app.dependency_overrides[get_model_provider] = ResourceProvider
    try:
        result = await send(api, chat_account)
        assert result[-1][0] == "error" and closed == [True]
        assert "private provider detail" not in str(result)
    finally:
        app.dependency_overrides.pop(get_model_provider, None)


async def test_failed_final_commit_does_not_emit_done_or_cache_a_success(api, chat_account):
    rejected = []
    def mark_completion(session, context):
        if any(isinstance(item, Message) and item.role == "assistant" and item.status == "completed"
               for item in session.identity_map.values()):
            session.info["reject_completion"] = True
    def reject_once(session):
        if session.info.get("reject_completion") and not rejected:
            rejected.append(True)
            raise SQLAlchemyError("测试最终提交失败")
    event.listen(Session, "after_flush", mark_completion)
    event.listen(Session, "before_commit", reject_once)
    try:
        result = await send(api, chat_account)
        assert result[-1][0] == "error"
        assert not any(name == "done" for name, data in result)
        assert rejected == [True]
    finally:
        event.remove(Session, "after_flush", mark_completion)
        event.remove(Session, "before_commit", reject_once)
    assert (await send(api, chat_account))[0][1]["context_rounds"] == 0


@pytest.mark.parametrize("scenario,code", [("failure", "generation_failed"), ("timeout", "timeout")])
async def test_failure_and_timeout_release_generation_and_exclude_context(api, chat_account, monkeypatch, scenario, code):
    monkeypatch.setattr(get_settings(), "model_timeout_seconds", 0.12)
    result = await send(api, chat_account, mock_scenario=scenario)
    assert result[-1][0] == "error" and result[-1][1]["code"] == code
    assert not any(name == "done" for name, data in result)
    history = (await api.get(chat_account["url"] + "/messages", headers=chat_account["headers"])).json()["items"]
    assert history[-1]["status"] == "failed" and history[-1]["content"]
    normal = await send(api, chat_account)
    assert normal[0][1]["context_rounds"] == 0 and normal[-1][0] == "done"


@pytest.mark.parametrize("scenario", ["failure", "timeout"])
async def test_fault_scenario_still_fails_with_one_large_chunk(api, chat_account, monkeypatch, scenario):
    settings = get_settings()
    monkeypatch.setattr(settings, "mock_chunk_chars", 256)
    monkeypatch.setattr(settings, "mock_failure_after_chunks", 100)
    monkeypatch.setattr(settings, "model_timeout_seconds", 0.1)
    result = await send(api, chat_account, content="火星天气", mock_scenario=scenario)
    assert result[-1][0] == "error"
    assert not any(name == "done" for name, data in result)


async def test_access_validation_happens_before_sse(api, chat_account):
    path = chat_account["url"] + "/chat"
    assert (await api.post(path, json={"content": "问题"})).status_code == 401
    assert (await api.post(path, headers=chat_account["headers"], json={"content": " ", "role": "assistant"})).status_code == 422
    other_name = f"other_{uuid4().hex[:20]}"
    credentials = {"username": other_name, "password": "Other-test-password"}
    await api.post("/auth/register", json=credentials)
    login = await api.post("/auth/token", data=credentials)
    foreign_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    foreign = await api.post(path, headers=foreign_headers, json={"content": "越权"})
    missing = await api.post(f"/conversations/{uuid4()}/chat", headers=foreign_headers, json={"content": "越权"})
    assert foreign.status_code == missing.status_code == 404 and foreign.json() == missing.json()
    assert "text/event-stream" not in foreign.headers["content-type"]
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == UUID(chat_account["id"]))) == 0


async def test_unknown_provider_is_explicitly_rejected(api, chat_account, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_provider", "real")
    response = await api.post(chat_account["url"] + "/chat", headers=chat_account["headers"], json={"content": "问题"})
    assert response.status_code == 503
    assert "尚未接入" in response.json()["detail"]


async def test_generation_busy_raw_write_and_delete_are_409_then_recover(api, chat_account):
    cid, uid = UUID(chat_account["id"]), UUID(chat_account["user_id"])
    with SessionLocal() as session:
        turn = prepare_turn(session, cid, uid, ChatRequest(content="校园卡补办"))
    for path, method, body in [("/chat", "POST", {"content": "并发"}), ("/messages", "POST", {"content": "并发"}), ("", "DELETE", None)]:
        response = await api.request(method, chat_account["url"] + path, headers=chat_account["headers"], json=body)
        assert response.status_code == 409
    with SessionLocal.begin() as session:
        session.get(Message, turn.assistant_message_id).generation_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    history = (await api.get(chat_account["url"] + "/messages", headers=chat_account["headers"])).json()["items"]
    assert history[-1]["status"] == "cancelled"
    next_turn = await send(api, chat_account)
    assert next_turn[-1][0] == "done" and next_turn[0][1]["context_rounds"] == 0
    # 过期旧任务不能覆盖新状态，也不能以 completed 写回。
    turn.parts = ["迟到的回复"]
    assert finish_turn(turn, "completed") is False


async def test_model_receives_materials_and_successful_history_without_db_checkout(api, chat_account):
    await send(api, chat_account)
    closed = []
    class InspectProvider:
        async def stream(self, data):
            assert data.guides and data.guides[0].materials and data.guides[0].source
            assert len(data.turns) == 1 and "模拟回复" in data.turns[0]["assistant"]
            assert engine.pool.checkedout() == 0, "生成期间不应占用数据库连接"
            try:
                yield "【模拟回复】测试模型接口"
            finally:
                closed.append(True)
    app.dependency_overrides[get_model_provider] = InspectProvider
    try:
        result = await send(api, chat_account)
        assert result[-1][0] == "done" and closed == [True]
    finally:
        app.dependency_overrides.pop(get_model_provider, None)


async def test_context_cache_version_limits_ttl_expiration_and_delete(api, chat_account, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_context_rounds", 2)
    monkeypatch.setattr(settings, "chat_context_max_chars", 180)
    cache = get_context_cache()
    key = cache.key(chat_account["user_id"], chat_account["id"])
    for index in range(3):
        await send(api, chat_account, f"校园卡补办第{index}问")
    payload = json.loads(cache.client.get(key))
    assert len(payload["turns"]) <= 2
    assert sum(len(t["user"]) + len(t["assistant"]) for t in payload["turns"]) <= 180
    assert 590 <= cache.client.ttl(key) <= 600
    cache.client.expire(key, 0)  # 只让这个明确的测试键过期。
    restored = await send(api, chat_account)
    assert restored[0][1]["context_rounds"] > 0
    payload = json.loads(cache.client.get(key))
    payload["version"] = -1
    payload["turns"] = []
    cache.client.set(key, json.dumps(payload), ex=30)
    assert (await send(api, chat_account))[0][1]["context_rounds"] > 0
    response = await api.delete(chat_account["url"], headers=chat_account["headers"])
    assert response.status_code == 204 and cache.client.get(key) is None


async def test_redis_failure_restores_history_and_still_completes(api, chat_account, monkeypatch):
    await send(api, chat_account)
    class Offline:
        def get(self, *args, **kwargs): raise RedisConnectionError("test offline")
        def set(self, *args, **kwargs): raise RedisConnectionError("test offline")
        def delete(self, *args, **kwargs): raise RedisConnectionError("test offline")
    cache = get_context_cache()
    with monkeypatch.context() as patch:
        patch.setattr(cache, "client", Offline())
        result = await send(api, chat_account)
        assert result[0][1]["context_rounds"] == 1 and result[-1][0] == "done"
    history = (await api.get(chat_account["url"] + "/messages", headers=chat_account["headers"])).json()["items"]
    assert len(history) == 4 and all(item["status"] == "completed" for item in history)


async def test_cache_corruption_and_foreign_metadata_cannot_supply_context(api, chat_account):
    await send(api, chat_account)
    cache = get_context_cache()
    key = cache.key(chat_account["user_id"], chat_account["id"])
    for value in ("not-json", b"\xff", json.dumps({"user_id": str(uuid4()), "turns": []})):
        cache.client.set(key, value, ex=20)
        assert (await send(api, chat_account))[0][1]["context_rounds"] > 0


async def test_page_is_available_and_configuration_contains_no_credentials(api):
    response = await api.get("/")
    assert response.status_code == 200 and "读取模型配置" in response.text and "演示指南" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    configuration = await api.get("/chat/config")
    assert configuration.json()["simulated"] is True
    assert configuration.json()["model"] == "mock-campus-v1"
    assert get_settings().jwt_secret_key.get_secret_value() not in configuration.text
