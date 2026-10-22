"""测试专用：创建唯一临时数据库，退出时只删除本次成功创建的库。"""

from contextlib import contextmanager
import os
import secrets
import sys
from uuid import uuid4

import psycopg
from psycopg import sql

from app.config import Settings, get_settings


def admin_connection(settings: Settings):
    return psycopg.connect(
        host=settings.postgres_host, port=settings.postgres_port,
        user=settings.postgres_user, password=settings.postgres_password.get_secret_value(),
        dbname="postgres", autocommit=True, connect_timeout=3,
    )


@contextmanager
def isolated_database():
    if "app.database" in sys.modules:
        raise RuntimeError("必须在导入应用和数据库模块之前建立测试隔离环境")
    # 测试自己生成密钥，不依赖或复用开发环境的 JWT 密钥。
    settings = Settings(jwt_secret_key=secrets.token_urlsafe(64))
    name = f"campus_assistant_test_{uuid4().hex}"
    with admin_connection(settings) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    environment = {
        "POSTGRES_DB": name,
        "JWT_SECRET_KEY": secrets.token_urlsafe(64),
        "GUIDE_CACHE_PREFIX": f"campus-assistant:test:guides:{uuid4().hex}",
        "CHAT_CONTEXT_PREFIX": f"campus-assistant:test:context:{uuid4().hex}",
        "MODEL_PROVIDER": "mock",
        "MODEL_API_KEY": "",
        "MOCK_DELAY_SECONDS": "0",
    }
    previous = {key: os.environ.get(key) for key in environment}
    try:
        os.environ.update(environment)
        get_settings.cache_clear()
        yield name
    finally:
        module = sys.modules.get("app.database")
        if module is not None:
            module.engine.dispose()
        try:
            # name 只来自上方成功创建的随机名称，不接受外部数据库名称。
            with admin_connection(settings) as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            get_settings.cache_clear()
