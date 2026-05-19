import asyncpg
import structlog
from contextlib import asynccontextmanager
from app.config import settings

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool = None


async def create_pool() -> asyncpg.Pool:
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            min_size=settings.MIN_DB_POOL_SIZE,
            max_size=settings.MAX_DB_POOL_SIZE,
            command_timeout=60,
        )
        logger.info("database_pool_created", host=settings.DB_HOST, database=settings.DB_NAME)
        return _pool
    except Exception as e:
        logger.error("database_pool_creation_failed", error=str(e))
        raise


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("database_pool_closed")


def get_pool() -> asyncpg.Pool:
    return _pool


@asynccontextmanager
async def acquire():
    async with _pool.acquire() as conn:
        yield conn
