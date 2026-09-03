"""Cloud-ready Streamable HTTP transport for AnyBridge."""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterable

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from .server import create_server


class _SessionApp:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope, receive, send) -> None:
        await self.manager.handle_request(scope, receive, send)


class BearerTokenMiddleware:
    """Small single-tenant auth boundary suitable for self-hosted deployments."""

    def __init__(self, app, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or not self.token or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {self.token}"
        if not secrets.compare_digest(supplied, expected):
            response = PlainTextResponse(
                "Missing or invalid AnyBridge bearer token.",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_remote_app(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    api_token: str | None = None,
    allowed_hosts: Iterable[str] = (),
    allowed_origins: Iterable[str] = (),
    idle_timeout: float = 900,
) -> object:
    """Create a stateful, authenticated Streamable HTTP MCP application."""
    if host not in {"127.0.0.1", "localhost", "::1"} and not api_token:
        raise ValueError("A bearer token is required when AnyBridge binds beyond localhost.")
    hosts = list(allowed_hosts) or [
        host,
        f"{host}:{port}",
        "localhost",
        f"localhost:{port}",
        "127.0.0.1",
        f"127.0.0.1:{port}",
    ]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=list(allowed_origins),
    )
    manager = StreamableHTTPSessionManager(
        app=create_server(),
        json_response=True,
        stateless=False,
        security_settings=security,
        session_idle_timeout=float(idle_timeout),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    async def health(request):
        return JSONResponse({"status": "ok", "service": "anybridge", "transport": "streamable-http"})

    app = Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Route("/mcp", endpoint=_SessionApp(manager)),
        ],
        lifespan=lifespan,
    )
    app.state.session_manager = manager
    return BearerTokenMiddleware(app, api_token)


def run_remote(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    api_token: str | None = None,
    allowed_hosts: Iterable[str] = (),
    allowed_origins: Iterable[str] = (),
    idle_timeout: float = 900,
) -> None:
    import uvicorn

    app = create_remote_app(
        host=host,
        port=port,
        api_token=api_token,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        idle_timeout=idle_timeout,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
