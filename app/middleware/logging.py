import time
import uuid
import logging
import structlog
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.requests import Request
from starlette.responses import Response

Path("logs").mkdir(exist_ok=True)


def setup_logging(log_level: str = "INFO"):
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    fmt = logging.Formatter("%(message)s")

    # api.log handler
    api_handler = TimedRotatingFileHandler(
        "logs/api.log",
        when="midnight",
        backupCount=30,
    )
    api_handler.setFormatter(fmt)
    root_logger.addHandler(api_handler)

    # error.log handler
    error_handler = TimedRotatingFileHandler(
        "logs/error.log",
        when="midnight",
        backupCount=90,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt)
    root_logger.addHandler(error_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)


class LoggingMiddleware:
    """Pure ASGI middleware — avoids BaseHTTPMiddleware's anyio task-group conflicts."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        drug_id = scope.get("path_params", {}).get("drug_id_1mg")
        if drug_id:
            structlog.contextvars.bind_contextvars(drug_id_1mg=drug_id)

        logger = structlog.get_logger(__name__)
        logger.info("request_received", endpoint=request.url.path)

        start = time.perf_counter()

        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request_completed",
                status_code=status_code,
                duration_ms=duration_ms,
            )
