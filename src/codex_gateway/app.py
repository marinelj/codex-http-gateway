import secrets
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .config import Settings
from .models import AcceptedRun, RunDetail, RunPage, RunSummary, SubmitRun, ThreadDetail
from .service import Gateway
from .store import GatewayError, Store


class BodyLimit:
    """Bound even chunked requests before JSON parsing; keep responses uncached."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def uncached(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message["headers"],
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        if scope["method"] == "POST":
            size = 0
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > 512 * 1024:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {"code": "body_too_large", "message": "Body exceeds 512 KiB"}
                        },
                        headers={"Connection": "close"},
                    )
                    return await response(scope, receive, uncached)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break

            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            return await self.app(scope, bounded_receive, uncached)
        return await self.app(scope, receive, uncached)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.prepare()
        token = settings.token()
        store = Store(settings)
        gateway = Gateway(settings, store)
        app.state.gateway = gateway
        app.state.token = token
        try:
            await gateway.start()
            yield
        finally:
            await gateway.stop()
            store.close()

    app = FastAPI(
        title="Codex HTTP Gateway",
        version=__version__,
        lifespan=lifespan,
        description="Submit Codex prompts asynchronously and retrieve persistent thread output.",
    )
    app.add_middleware(BodyLimit)
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), request.app.state.token.encode()
        ):
            raise HTTPException(
                401,
                "Valid gateway bearer token required",
                headers={
                    "WWW-Authenticate": "Bearer",
                },
            )

    @app.exception_handler(GatewayError)
    async def gateway_error(_request: Request, error: GatewayError):
        return JSONResponse(
            status_code=error.status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                }
            },
            headers={"Retry-After": "2"} if error.status == 429 else None,
        )

    @app.get("/healthz", tags=["health"])
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    router = APIRouter(prefix="/v1", dependencies=[Depends(authenticate)])

    def gateway(request: Request) -> Gateway:
        return request.app.state.gateway

    @router.post("/runs", status_code=202, response_model=AcceptedRun, tags=["runs"])
    async def submit(
        body: SubmitRun,
        request: Request,
        response: Response,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None,
    ):
        run = await gateway(request).submit(body, idempotency_key)
        prefix = request.scope.get("root_path", "").rstrip("/")
        url = f"{prefix}/v1/runs/{run['run_id']}"
        response.headers["Location"] = url
        return {
            **run,
            "status_url": url,
            "thread_url": (f"{prefix}/v1/threads/{run['thread_id']}" if run["thread_id"] else None),
        }

    @router.get("/runs", response_model=RunPage, tags=["runs"])
    async def list_runs(
        request: Request,
        thread_id: UUID | None = None,
        after: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return gateway(request).store.runs(
            thread_id=str(thread_id) if thread_id else None,
            after=after,
            limit=limit,
        )

    @router.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
    async def get_run(
        run_id: str,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ):
        store = gateway(request).store
        return {**store.get(run_id), **store.events(run_id=run_id, after=after, limit=limit)}

    @router.post("/runs/{run_id}/cancel", response_model=RunSummary, tags=["runs"])
    async def cancel(run_id: str, request: Request):
        return await gateway(request).cancel(run_id)

    @router.get("/threads/{thread_id}", response_model=ThreadDetail, tags=["threads"])
    async def get_thread(
        thread_id: UUID,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ):
        store = gateway(request).store
        latest = store.latest(str(thread_id))
        return {
            "thread_id": str(thread_id),
            "status": latest["status"],
            "latest_run": latest,
            **store.events(thread_id=str(thread_id), after=after, limit=limit),
        }

    app.include_router(router)
    return app
