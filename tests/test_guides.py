"""使用真实 PostgreSQL/Redis 验收；只删除本测试独立前缀下的明确键。"""

import socket
import time
from uuid import uuid4

import anyio
import httpx
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import event, func, inspect, select

from app.cache import GUIDE_CACHE_TTL, GuideCache, create_redis_client, get_guide_cache
from app.config import PROJECT_ROOT, Settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Guide
from scripts.import_demo_guides import import_demo_guides

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RecordingCache(GuideCache):
    def __init__(self, client: Redis, prefix: str):
        super().__init__(client, prefix)
        self.touched_keys: set[str] = set()

    def key(self, guide_id: int) -> str:
        key = super().key(guide_id)
        self.touched_keys.add(key)
        return key


@pytest.fixture
def cache():
    client = create_redis_client()
    assert client.ping(), "请先启动 Redis"
    cache = RecordingCache(client, f"campus-assistant:test:guides:{uuid4().hex}")
    try:
        yield cache
    finally:
        # 不执行 FLUSHDB/FLUSHALL，也不扫描或删除业务键。
        if cache.touched_keys:
            client.delete(*cache.touched_keys)
        client.close()


@pytest.fixture
async def api(cache):
    app.dependency_overrides[get_guide_cache] = lambda: cache
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                trust_env=False,
            ) as client:
                yield client
    finally:
        app.dependency_overrides.pop(get_guide_cache, None)


@pytest.fixture
def guide_id():
    with SessionLocal() as session:
        value = session.scalar(select(Guide.id).where(Guide.demo_key == "demo-campus-card"))
    assert value is not None, "请先运行 alembic upgrade head 和演示数据导入命令"
    return value


@pytest.fixture
def sql_reads():
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if "FROM guides" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_migration_is_at_head_and_columns_exist():
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == ScriptDirectory.from_config(config).get_current_head()
    columns = {column["name"] for column in inspect(engine).get_columns("guides")}
    assert {"id", "title", "category", "department", "steps", "materials", "source", "updated_at", "is_demo", "demo_key"} == columns
    constraints = inspect(engine).get_unique_constraints("guides")
    assert any(item["name"] == "uq_guides_demo_key" for item in constraints)


def test_import_twice_preserves_records():
    def snapshot():
        with SessionLocal() as session:
            count = session.scalar(select(func.count()).select_from(Guide))
            demos = session.execute(
                select(Guide.id, Guide.demo_key, Guide.updated_at)
                .where(Guide.demo_key.like("demo-%"))
                .order_by(Guide.id)
            ).all()
            return count, demos

    before = snapshot()
    assert len(before[1]) == 8
    assert import_demo_guides() == 0
    assert import_demo_guides() == 0
    assert snapshot() == before


async def test_health(api):
    response = await api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_list_pagination(api):
    first = await api.get("/guides", params={"page": 1, "page_size": 3})
    second = await api.get("/guides", params={"page": 2, "page_size": 3})
    assert first.status_code == second.status_code == 200
    first, second = first.json(), second.json()
    assert first["total"] == second["total"] >= 8
    assert first["page"] == 1 and second["page"] == 2
    assert first["page_size"] == second["page_size"] == 3
    assert len(first["items"]) == len(second["items"]) == 3
    first_ids = [item["id"] for item in first["items"]]
    second_ids = [item["id"] for item in second["items"]]
    assert max(first_ids) < min(second_ids)
    assert (await api.get("/guides", params={"page": 1000000})).json()["items"] == []


async def test_keyword_category_and_combined_filters(api):
    result = (await api.get("/guides", params={"keyword": " 校园卡 ", "category": "校园生活"})).json()
    assert result["total"] >= 1
    assert all("校园卡" in item["title"] and item["category"] == "校园生活" for item in result["items"])
    assert (await api.get("/guides", params={"keyword": "校园卡", "category": "学籍教务"})).json()["total"] == 0
    categories = (await api.get("/guides", params={"category": "学籍教务"})).json()
    assert categories["total"] >= 2
    assert all(item["category"] == "学籍教务" for item in categories["items"])
    assert (await api.get("/guides", params={"keyword": uuid4().hex})).json()["total"] == 0


@pytest.mark.parametrize("keyword", ["%", "_", "' OR 1=1 --"])
async def test_keyword_is_literal_not_sql_pattern(api, keyword):
    response = await api.get("/guides", params={"keyword": keyword})
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"keyword": "x" * 101}])
async def test_invalid_query_parameters(api, params):
    assert (await api.get("/guides", params=params)).status_code == 422


@pytest.mark.parametrize("guide_id", ["0", "-1", "abc", "2147483648"])
async def test_invalid_guide_id(api, guide_id):
    assert (await api.get(f"/guides/{guide_id}")).status_code == 422


async def test_detail_response(api, guide_id):
    response = await api.get(f"/guides/{guide_id}")
    assert response.status_code == 200
    guide = response.json()
    assert guide["id"] == guide_id and guide["title"] == "校园卡补办"
    assert guide["is_demo"] is True and "演示" in guide["source"]
    assert guide["steps"] and guide["materials"] and guide["updated_at"]
    assert guide["department"] and guide["category"]


async def test_missing_guide_returns_404_without_caching(api, cache):
    with SessionLocal() as session:
        missing = session.scalar(select(func.max(Guide.id))) + 100000
    response = await api.get(f"/guides/{missing}")
    assert response.status_code == 404
    assert response.json() == {"detail": "办事指南不存在"}
    assert cache.client.get(cache.key(missing)) is None


async def test_cache_hit_avoids_postgres_query(api, cache, guide_id, sql_reads):
    first = await api.get(f"/guides/{guide_id}")
    assert first.status_code == 200
    assert len(sql_reads) == 1
    assert 295 <= cache.client.ttl(cache.key(guide_id)) <= GUIDE_CACHE_TTL == 300
    second = await api.get(f"/guides/{guide_id}")
    assert second.json() == first.json()
    assert len(sql_reads) == 1, "缓存命中不应查询 PostgreSQL"


async def test_expired_cache_falls_back_and_is_refilled(api, cache, guide_id, sql_reads):
    first = await api.get(f"/guides/{guide_id}")
    key = cache.key(guide_id)
    assert 295 <= cache.client.ttl(key) <= 300
    # 只缩短这个明确测试键的寿命，实际等待 Redis 将其过期。
    assert cache.client.expire(key, 1)
    deadline = time.monotonic() + 4
    while cache.client.exists(key) and time.monotonic() < deadline:
        await anyio.sleep(0.05)
    assert cache.client.ttl(key) == -2
    reads = len(sql_reads)
    second = await api.get(f"/guides/{guide_id}")
    assert second.status_code == 200 and second.json() == first.json()
    assert len(sql_reads) == reads + 1
    assert 295 <= cache.client.ttl(key) <= 300


@pytest.mark.parametrize("value", ["not-json", "{}", b"\xff"])
async def test_invalid_cache_falls_back(api, cache, guide_id, sql_reads, value):
    cache.client.set(cache.key(guide_id), value, ex=30)
    response = await api.get(f"/guides/{guide_id}")
    assert response.status_code == 200
    assert response.json()["id"] == guide_id
    assert len(sql_reads) == 1
    assert 295 <= cache.client.ttl(cache.key(guide_id)) <= 300


async def test_unavailable_redis_falls_back_to_real_postgres(api, cache, guide_id, sql_reads, caplog):
    # 占用但不监听一个临时端口，产生真实的连接拒绝；不用停止共享 Redis。
    with socket.socket() as closed_port:
        closed_port.bind(("127.0.0.1", 0))
        unavailable = Redis(
            host="127.0.0.1",
            port=closed_port.getsockname()[1],
            socket_timeout=0.1,
            socket_connect_timeout=0.1,
            retry=Retry(NoBackoff(), 0),
        )
        try:
            with pytest.raises(RedisError):
                unavailable.ping()
            app.dependency_overrides[get_guide_cache] = lambda: GuideCache(unavailable, cache.prefix)
            response = await api.get(f"/guides/{guide_id}")
            assert response.status_code == 200
            assert response.json()["title"] == "校园卡补办"
            assert len(sql_reads) == 1
            assert "Redis 读取失败" in caplog.text
            assert "Redis 写入失败" in caplog.text
        finally:
            unavailable.close()


def test_settings_accept_environment_and_special_password(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTGRES_DB", "environment_test")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.postgres_db == "environment_test"
    special = Settings(
        _env_file=None,
        postgres_db="demo",
        postgres_user="demo",
        postgres_password="demo@:/%#password",
    )
    assert special.database_url.password == "demo@:/%#password"
    assert "demo@:/%#password" not in special.model_dump_json()
