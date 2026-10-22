"""详情缓存：Redis 故障、过期或内容损坏时均视为未命中。"""

import logging
from functools import lru_cache

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from app.config import get_settings
from app.schemas import GuideRead

logger = logging.getLogger(__name__)
GUIDE_CACHE_TTL = 300


class GuideCache:
    def __init__(self, client: Redis, prefix: str):
        self.client = client
        self.prefix = prefix

    def key(self, guide_id: int) -> str:
        return f"{self.prefix}:{guide_id}"

    def get(self, guide_id: int) -> GuideRead | None:
        try:
            value = self.client.get(self.key(guide_id))
        except (RedisError, UnicodeError) as exc:
            logger.warning("Redis 读取失败，回退到 PostgreSQL（%s）", type(exc).__name__)
            return None
        if value is None:
            return None
        try:
            guide = GuideRead.model_validate_json(value)
            if guide.id == guide_id:
                return guide
        except ValueError:
            pass
        logger.warning("指南 %s 的缓存内容无效，回退到 PostgreSQL", guide_id)
        return None

    def set(self, guide: GuideRead) -> None:
        try:
            self.client.set(self.key(guide.id), guide.model_dump_json(), ex=GUIDE_CACHE_TTL)
        except RedisError as exc:
            # 数据库查询已经成功；缓存写入失败不能让接口返回 500。
            logger.warning("Redis 写入失败，直接返回数据库结果（%s）", type(exc).__name__)


def create_redis_client() -> Redis:
    settings = get_settings()
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=(settings.redis_password.get_secret_value() or None)
        if settings.redis_password is not None
        else None,
        socket_connect_timeout=settings.redis_socket_timeout,
        socket_timeout=settings.redis_socket_timeout,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


@lru_cache
def get_guide_cache() -> GuideCache:
    return GuideCache(create_redis_client(), get_settings().guide_cache_prefix)
