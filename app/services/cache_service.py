from app.cache import get_cached, set_cached, build_key
from app.config import settings


async def get_from_cache(key: str):
    return await get_cached(key)


async def save_to_cache(key: str, value, ttl: int = None):
    if ttl is None:
        ttl = settings.CACHE_TTL
    await set_cached(key, value, ttl=ttl)


def make_key(prefix: str, *args) -> str:
    return build_key(prefix, *args)
