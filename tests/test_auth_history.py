"""认证、归属校验及事务一致性，使用 conftest 创建的独立 PostgreSQL 数据库。"""

import asyncio
from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cache import GuideCache
from app.config import get_settings
from app.database import SessionLocal
from app.models import Conversation, Message, User
from app.security import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, verify_password

pytestmark = pytest.mark.anyio
TEST_PASSWORD = "Campus-test-password-2026"


async def new_account(api):
    username = f"u_{uuid4().hex[:24]}"
    registered = await api.post("/auth/register", json={"username": username, "password": TEST_PASSWORD})
    assert registered.status_code == 201
    login = await api.post("/auth/token", data={"username": username, "password": TEST_PASSWORD})
    assert login.status_code == 200
    return {
        "user": registered.json(), "username": username,
        "token": login.json()["access_token"],
        "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }


@pytest.fixture
async def account(api):
    return await new_account(api)


async def new_conversation(api, account, title="测试会话"):
    response = await api.post("/conversations", headers=account["headers"], json={"title": title})
    assert response.status_code == 201
    return response.json()


def token_for(user_id, **changes):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id), "iat": now - timedelta(seconds=2),
        "exp": now + timedelta(minutes=5), "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE, "type": "access",
    }
    claims.update(changes)
    return claims


async def test_registration_duplicate_and_password_hash(api, caplog):
    username = f"u_{uuid4().hex[:24]}"
    payload = {"username": username, "password": TEST_PASSWORD}
    response = await api.post("/auth/register", json=payload)
    assert response.status_code == 201
    assert set(response.json()) == {"id", "username", "created_at"}
    duplicate = await api.post("/auth/register", json=dict(payload, username=f" {username.upper()} "))
    assert duplicate.status_code == 409
    with SessionLocal() as session:
        users = session.scalars(select(User).where(User.username == username)).all()
        assert len(users) == 1
        assert users[0].hashed_password.startswith("$argon2id$")
        assert users[0].hashed_password != TEST_PASSWORD
        assert verify_password(TEST_PASSWORD, users[0].hashed_password)
        assert users[0].hashed_password not in response.text
    assert TEST_PASSWORD not in response.text + duplicate.text + caplog.text


async def test_concurrent_duplicate_registration(api):
    payload = {"username": f"u_{uuid4().hex[:24]}", "password": TEST_PASSWORD}
    responses = await asyncio.gather(
        api.post("/auth/register", json=payload), api.post("/auth/register", json=payload)
    )
    assert sorted(response.status_code for response in responses) == [201, 409]


async def test_login_me_and_swagger(api, account):
    response = await api.get("/users/me", headers=account["headers"])
    assert response.status_code == 200 and response.json() == account["user"]
    login = await api.post("/auth/token", data={"username": account["username"].upper(), "password": TEST_PASSWORD})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    assert login.json()["expires_in"] == get_settings().access_token_expire_minutes * 60
    assert login.headers["cache-control"] == "no-store"
    claims = jwt.decode(
        login.json()["access_token"], get_settings().jwt_secret_key.get_secret_value(),
        algorithms=[JWT_ALGORITHM], audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
    )
    assert claims["sub"] == account["user"]["id"] and claims["exp"] > claims["iat"]
    assert TEST_PASSWORD not in str(claims)
    schema = (await api.get("/openapi.json")).json()
    flow = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"]["password"]
    assert flow["tokenUrl"] == "auth/token"
    assert schema["paths"]["/conversations"]["post"]["security"] == [{"OAuth2PasswordBearer": []}]


async def test_wrong_password_and_unknown_user_are_indistinguishable(api, account):
    wrong = await api.post("/auth/token", data={"username": account["username"], "password": "wrong-password"})
    unknown = await api.post("/auth/token", data={"username": uuid4().hex, "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("kind", ["malformed", "expired", "wrong_key", "no_exp", "wrong_aud", "wrong_iss", "wrong_type", "deleted_user", "none_algorithm"])
async def test_invalid_and_expired_tokens(api, account, kind, caplog):
    claims = token_for(account["user"]["id"])
    key = get_settings().jwt_secret_key.get_secret_value()
    if kind == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif kind == "no_exp":
        claims.pop("exp")
    elif kind == "wrong_aud":
        claims["aud"] = "other-service"
    elif kind == "wrong_iss":
        claims["iss"] = "other-issuer"
    elif kind == "wrong_type":
        claims["type"] = "refresh"
    elif kind == "deleted_user":
        claims["sub"] = str(uuid4())
    token = jwt.encode(claims, secrets.token_urlsafe(64) if kind == "wrong_key" else key, algorithm=JWT_ALGORITHM)
    if kind == "malformed":
        token = "not-a-valid-token"
    if kind == "none_algorithm":
        token = jwt.encode(claims, key=None, algorithm="none")
    response = await api.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert key not in response.text + caplog.text
    assert token not in response.text + caplog.text


@pytest.mark.parametrize("method,path", [
    ("GET", "/users/me"), ("POST", "/conversations"), ("GET", "/conversations"),
    ("GET", "/conversations/{id}"), ("DELETE", "/conversations/{id}"),
    ("POST", "/conversations/{id}/messages"), ("GET", "/conversations/{id}/messages"),
])
async def test_unauthenticated_access(api, method, path):
    response = await api.request(method, path.format(id=uuid4()), json={"content": "消息", "title": "标题"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_validation_errors_never_echo_credentials(api, caplog):
    secret = get_settings().jwt_secret_key.get_secret_value()
    payload = {"username": "valid_username", "password": TEST_PASSWORD, "jwt_secret_key": secret}
    first = await api.post("/auth/register", json=payload)
    second = await api.post("/auth/register", json={"username": "valid_username", "password": TEST_PASSWORD * 10})
    third = await api.post("/auth/token", data={"password": TEST_PASSWORD})
    assert first.status_code == second.status_code == third.status_code == 422
    assert TEST_PASSWORD not in first.text + second.text + third.text + caplog.text
    assert secret not in first.text + caplog.text
    assert all("input" not in item and "ctx" not in item for item in first.json()["detail"])


@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None), ("DELETE", "", None),
    ("POST", "/messages", {"content": "不能写入别人的会话"}), ("GET", "/messages", None),
])
async def test_foreign_and_missing_conversations_both_return_404(api, account, method, suffix, body):
    owner = await new_account(api)
    conversation = await new_conversation(api, owner)
    saved = await api.post(f"/conversations/{conversation['id']}/messages", headers=owner["headers"], json={"content": "原有消息"})
    assert saved.status_code == 201
    foreign = await api.request(method, f"/conversations/{conversation['id']}{suffix}", headers=account["headers"], json=body)
    missing = await api.request(method, f"/conversations/{uuid4()}{suffix}", headers=account["headers"], json=body)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "会话不存在"}
    assert (await api.get("/conversations", headers=account["headers"])).json()["total"] == 0
    with SessionLocal() as session:
        assert session.get(Conversation, UUID(conversation["id"])) is not None
        assert session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == UUID(conversation["id"]))) == 1


async def test_conversation_sorting_pagination_and_ownership(api, account):
    first = await new_conversation(api, account, "第一个")
    second = await new_conversation(api, account, "第二个")
    await api.post(f"/conversations/{first['id']}/messages", headers=account["headers"], json={"content": "更新第一个"})
    page1 = (await api.get("/conversations?page_size=1", headers=account["headers"])).json()
    page2 = (await api.get("/conversations?page=2&page_size=1", headers=account["headers"])).json()
    assert page1["total"] == page2["total"] == 2
    assert page1["items"][0]["id"] == first["id"]
    assert page2["items"][0]["id"] == second["id"]
    assert page1["items"][0]["updated_at"] > first["updated_at"]
    # 相同更新时间时也有确定的次序。
    with SessionLocal.begin() as session:
        session.execute(update(Conversation).where(Conversation.user_id == UUID(account["user"]["id"])).values(updated_at=datetime.now(timezone.utc)))
    tied = (await api.get("/conversations", headers=account["headers"])).json()["items"]
    assert [item["id"] for item in tied] == sorted([first["id"], second["id"]], reverse=True)


async def test_messages_persist_in_order_and_update_conversation(api, account):
    conversation = await new_conversation(api, account)
    url = f"/conversations/{conversation['id']}/messages"
    saved = []
    for text in ("第一条", "第二条", "  第三条保留空格  "):
        response = await api.post(url, headers=account["headers"], json={"content": text})
        assert response.status_code == 201 and response.json()["role"] == "user"
        saved.append(response.json())
    with SessionLocal.begin() as session:
        session.execute(update(Message).where(Message.conversation_id == UUID(conversation["id"])).values(created_at=datetime.now(timezone.utc)))
    first = (await api.get(url + "?page_size=2", headers=account["headers"])).json()
    second = (await api.get(url + "?page=2&page_size=2", headers=account["headers"])).json()
    assert first["total"] == second["total"] == 3
    assert [item["id"] for item in first["items"] + second["items"]] == [item["id"] for item in saved]
    assert second["items"][0]["content"] == "  第三条保留空格  "
    detail = (await api.get(f"/conversations/{conversation['id']}", headers=account["headers"])).json()
    assert detail["updated_at"] > conversation["updated_at"]
    assert (await api.get(url + "?page=100", headers=account["headers"])).json()["items"] == []


@pytest.mark.parametrize("body", [
    {"title": ""}, {"title": "   "}, {"title": "长" * 101},
    {"title": "冒充归属", "user_id": str(uuid4())},
])
async def test_conversation_validation_and_server_side_owner(api, account, body):
    response = await api.post("/conversations", headers=account["headers"], json=body)
    assert response.status_code == 422
    assert (await api.get("/conversations", headers=account["headers"])).json()["total"] == 0


async def test_message_maximum_length_is_accepted(api, account):
    conversation = await new_conversation(api, account)
    content = "文" * 4000
    response = await api.post(
        f"/conversations/{conversation['id']}/messages",
        headers=account["headers"], json={"content": content},
    )
    assert response.status_code == 201
    assert response.json()["content"] == content


@pytest.mark.parametrize("body", [
    {}, {"content": ""}, {"content": " \n\t"}, {"content": "长" * 4001},
    {"content": "消息", "role": "assistant"}, {"content": "消息", "user_id": str(uuid4())},
    {"content": "消息", "conversation_id": str(uuid4())},
])
async def test_message_validation_prevents_identity_and_role_spoofing(api, account, body):
    conversation = await new_conversation(api, account)
    url = f"/conversations/{conversation['id']}/messages"
    response = await api.post(url, headers=account["headers"], json=body)
    assert response.status_code == 422
    assert (await api.get(url, headers=account["headers"])).json()["total"] == 0


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "page_size=0", "page=1000001"])
async def test_history_pagination_limits(api, account, query):
    conversation = await new_conversation(api, account)
    assert (await api.get("/conversations?" + query, headers=account["headers"])).status_code == 422
    assert (await api.get(f"/conversations/{conversation['id']}/messages?{query}", headers=account["headers"])).status_code == 422


async def test_deletion_cascades_only_its_messages(api, account):
    removed = await new_conversation(api, account)
    kept = await new_conversation(api, account)
    for conversation in (removed, kept):
        await api.post(f"/conversations/{conversation['id']}/messages", headers=account["headers"], json={"content": "保存在数据库"})
    response = await api.delete(f"/conversations/{removed['id']}", headers=account["headers"])
    assert response.status_code == 204 and response.content == b""
    assert (await api.get(f"/conversations/{removed['id']}/messages", headers=account["headers"])).status_code == 404
    with SessionLocal() as session:
        assert session.get(Conversation, UUID(removed["id"])) is None
        assert session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == UUID(removed["id"]))) == 0
        assert session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == UUID(kept["id"]))) == 1


@pytest.mark.parametrize("operation", ["write", "delete"])
async def test_failed_transaction_preserves_conversation_and_messages(api, account, monkeypatch, operation):
    conversation = await new_conversation(api, account)
    url = f"/conversations/{conversation['id']}"
    assert (await api.post(url + "/messages", headers=account["headers"], json={"content": "保留的消息"})).status_code == 201
    before = (await api.get(url, headers=account["headers"])).json()

    def fail_after_flush(session):
        session.flush()
        raise SQLAlchemyError("测试提交故障")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_after_flush)
        if operation == "write":
            response = await api.post(url + "/messages", headers=account["headers"], json={"content": "不应持久化"})
        else:
            response = await api.delete(url, headers=account["headers"])
        assert response.status_code == 503
    assert (await api.get(url, headers=account["headers"])).json() == before
    messages = (await api.get(url + "/messages", headers=account["headers"])).json()
    assert messages["total"] == 1 and messages["items"][0]["content"] == "保留的消息"


async def test_concurrent_delete_and_write_leave_no_orphans(api, account):
    conversation = await new_conversation(api, account)
    url = f"/conversations/{conversation['id']}"
    deleted, written = await asyncio.gather(
        api.delete(url, headers=account["headers"]),
        api.post(url + "/messages", headers=account["headers"], json={"content": "并发写入"}),
    )
    assert deleted.status_code == 204 and written.status_code in (201, 404)
    with SessionLocal() as session:
        assert session.get(Conversation, UUID(conversation["id"])) is None
        assert session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == UUID(conversation["id"]))) == 0


async def test_history_does_not_use_redis(api, account, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("历史记录不应使用 Redis")
    monkeypatch.setattr(GuideCache, "get", forbidden)
    monkeypatch.setattr(GuideCache, "set", forbidden)
    conversation = await new_conversation(api, account)
    url = f"/conversations/{conversation['id']}/messages"
    assert (await api.post(url, headers=account["headers"], json={"content": "PostgreSQL 是唯一可靠来源"})).status_code == 201
    assert (await api.get(url, headers=account["headers"])).json()["total"] == 1


async def test_repeated_validation_errors_do_not_change_authentication(api, account, monkeypatch):
    errors = []
    decode = jwt.decode
    def checked_decode(*args, **kwargs):
        try:
            return decode(*args, **kwargs)
        except Exception as exc:
            errors.append(type(exc).__name__)
            raise
    monkeypatch.setattr(jwt, "decode", checked_decode)
    for _ in range(100):
        invalid = await api.get("/conversations?page_size=101", headers=account["headers"])
        me = await api.get("/users/me", headers=account["headers"])
        assert invalid.status_code == 422, errors
        assert me.status_code == 200, errors
    assert errors == []
