import json
import structlog
from typing import Any, Optional
import aioredis
from app.config import settings

logger = structlog.get_logger(__name__)

_redis: aioredis.Redis = None


async def create_redis():
    global _redis
    try:
        _redis = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        await _redis.ping()
        logger.info("redis_connected", host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    except Exception as e:
        logger.warning("redis_connection_failed", error=str(e))
        _redis = None


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        logger.info("redis_closed")


async def get_cached(key: str) -> Optional[dict]:
    if _redis is None:
        return None
    try:
        value = await _redis.get(key)
        if value is not None:
            logger.info("cache_hit", key=key)
            return json.loads(value)
        logger.info("cache_miss", key=key)
        return None
    except Exception as e:
        logger.warning("cache_get_error", key=key, error=str(e))
        return None


async def set_cached(key: str, value: Any, ttl: int = None) -> bool:
    if _redis is None:
        return False
    if ttl is None:
        ttl = settings.CACHE_TTL
    try:
        await _redis.set(key, json.dumps(value), ex=ttl)
        logger.info("cache_set", key=key, ttl=ttl)
        return True
    except Exception as e:
        logger.warning("cache_set_error", key=key, error=str(e))
        return False


async def delete_cached(key: str) -> bool:
    if _redis is None:
        return False
    try:
        await _redis.delete(key)
        return True
    except Exception as e:
        logger.warning("cache_delete_error", key=key, error=str(e))
        return False


def is_connected() -> bool:
    return _redis is not None


def build_key(prefix: str, *args) -> str:
    return ":".join([prefix] + [str(a) for a in args])
