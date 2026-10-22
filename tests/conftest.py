"""收集测试前切换到临时数据库；所有测试均不连接开发数据库的表。"""

import httpx
import pytest

from scripts.test_database import isolated_database


def pytest_configure(config):
    context = isolated_database()
    try:
        name = context.__enter__()
    except Exception as exc:
        raise pytest.UsageError(
            f"无法建立独立测试数据库（{type(exc).__name__}）；请检查 PostgreSQL 连接和 CREATEDB 权限"
        ) from None
    config._campus_database_context = context
    config._campus_database_name = name


def pytest_unconfigure(config):
    context = getattr(config, "_campus_database_context", None)
    if context is not None:
        context.__exit__(None, None, None)


@pytest.fixture(scope="session", autouse=True)
def initialize_database():
    from alembic import command
    from alembic.config import Config
    from app.config import PROJECT_ROOT, get_settings
    from app.database import engine
    from scripts.import_demo_guides import import_demo_guides

    assert get_settings().postgres_db.startswith("campus_assistant_test_")
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    assert import_demo_guides() == 8
    yield
    engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def api():
    from app.main import app

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver", trust_env=False,
        ) as client:
            yield client
