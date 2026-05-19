import time
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.requests import Request


class TimingMiddleware:
    """Pure ASGI middleware — avoids BaseHTTPMiddleware's anyio task-group conflicts."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                headers = list(message.get("headers", []))
                headers.append((b"x-response-time", f"{duration_ms}ms".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
