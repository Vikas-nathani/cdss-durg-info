import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.db import create_pool, close_pool, get_pool
from app.cache import create_redis, close_redis, is_connected as redis_connected
from app.middleware.logging import LoggingMiddleware, setup_logging
from app.middleware.timing import TimingMiddleware
from app.middleware.auth import AuthMiddleware
from app.routers import label, interactions, drug_classes, dosing

setup_logging(settings.LOG_LEVEL)
logger = structlog.get_logger(__name__)

limiter = Limiter(
    key_func=lambda request: request.headers.get("X-API-Key", get_remote_address(request))
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup_begin")
    await create_pool()
    await create_redis()

    if settings.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)
        logger.info("sentry_initialized")

    logger.info("startup_complete")
    yield
    logger.info("shutdown_begin")
    await close_pool()
    await close_redis()
    logger.info("shutdown_complete")


app = FastAPI(
    title="CDSS Drug Info API",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Pure ASGI middlewares — added in reverse order (last added = outermost)
app.add_middleware(AuthMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(TimingMiddleware)

app.include_router(label.router, prefix="/api/v1")
app.include_router(interactions.router, prefix="/api/v1")
app.include_router(drug_classes.router, prefix="/api/v1")
app.include_router(dosing.router, prefix="/api/v1")


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema["components"]["securitySchemes"] = {
        "ApiKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }
    }
    schema["security"] = [{"ApiKeyHeader": []}]
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi


@app.get("/health")
async def health_check():
    pool = get_pool()
    db_status = "disconnected"
    if pool:
        try:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            db_status = "connected"
        except Exception:
            db_status = "disconnected"

    redis_status = "connected" if redis_connected() else "disconnected"

    return {
        "status": "healthy",
        "database": db_status,
        "redis": redis_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
