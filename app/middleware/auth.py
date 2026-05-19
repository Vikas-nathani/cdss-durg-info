import json
import structlog
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.requests import Request
from app.config import settings

logger = structlog.get_logger(__name__)

SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware:
    """Pure ASGI middleware — avoids BaseHTTPMiddleware's anyio task-group conflicts."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        if request.url.path in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            logger.warning("missing_api_key", path=request.url.path)
            await self._send_401(send, "Missing X-API-Key header")
            return

        if api_key != settings.API_KEY:
            logger.warning("invalid_api_key", path=request.url.path)
            await self._send_401(send, "Invalid API key")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_401(send: Send, message: str) -> None:
        body = json.dumps({
            "success": False,
            "error_code": "UNAUTHORIZED",
            "message": message,
            "request_id": "",
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
