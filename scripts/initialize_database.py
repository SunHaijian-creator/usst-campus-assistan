"""独立单次初始化：串行增量迁移 + 幂等导入；应用进程不执行迁移。"""

import json

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import PROJECT_ROOT

INITIALIZE_LOCK = 734821026


def initialize_database():
    from app.database import engine
    from app.reference_loader import import_references
    from scripts.import_demo_guides import import_demo_guides

    # 会话级锁覆盖迁移及导入；独立命令并发执行也会串行，最多等待60秒。
    # 连接关闭时 PostgreSQL 自动释放锁；不重置、截断或删除已有记录。
    with engine.connect() as lock:
        lock.execute(text("SET lock_timeout = '60s'"))
        lock.execute(text("SELECT pg_advisory_lock(:key)"), {"key": INITIALIZE_LOCK})
        lock.commit()
        try:
            command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
            return {"migration": "head", "demo_guides_added": import_demo_guides(), **import_references()}
        finally:
            lock.rollback()
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": INITIALIZE_LOCK})
            lock.commit()


def main():
    try:
        print(json.dumps(initialize_database(), ensure_ascii=False))
    except Exception as exc:
        # 数据库异常可能包含连接详情，不输出异常正文/URL/凭据。
        raise SystemExit(f"初始化失败（{type(exc).__name__}）；请检查配置与数据库状态，未重置已有数据") from None
    finally:
        from app.database import engine
        engine.dispose()


if __name__ == "__main__":
    main()
