"""FastAPI 앱 팩토리. 핸들러는 검증만 하고 상태는 Store를 통해서만 만진다."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..errors import MiniRedisError
from ..store import Store
from ..persistence.manager import Persistence
from .routes import router

log = logging.getLogger(__name__)


class RawPathMiddleware:
    """퍼센트 인코딩된 경로를 라우팅 전에 디코딩하지 않게 한다.

    서버는 경로를 먼저 디코딩해서 `/keys/a%2Fb`가 `/keys/a/b`가 된다. 그러면 키 하나가
    경로 두 칸으로 갈라져 라우팅이 틀린다. 그래서 인코딩된 원본(raw_path)을 경로로 쓰고,
    핸들러가 세그먼트를 한 번만 디코딩한다.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            raw = scope.get("raw_path")
            if raw:
                try:
                    scope["path"] = raw.split(b"?", 1)[0].decode("ascii")
                except UnicodeDecodeError:
                    pass  # ASCII가 아닌 원본은 서버가 디코딩한 경로를 그대로 쓴다
        await self.app(scope, receive, send)


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


def create_app(store: Store | None = None, persistence: Persistence | None = None) -> FastAPI:
    store = store or Store()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 복구가 끝난 뒤에 만료 스레드를 켜요(spec).
        if persistence:
            persistence.start(store)
        store.start_expiry()
        try:
            yield
        finally:
            store.stop_expiry()
            if persistence:
                persistence.stop()

    app = FastAPI(title="Mini Redis", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(RawPathMiddleware)
    app.include_router(router)

    @app.exception_handler(MiniRedisError)
    async def _mini_redis_error(_: Request, exc: MiniRedisError):
        return _error(exc.status, exc.code, exc.message, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return _error(400, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다")

    @app.exception_handler(Exception)
    async def _unexpected_error(_: Request, exc: Exception):
        log.exception("처리되지 않은 오류")
        return _error(500, "INTERNAL_ERROR", "내부 오류가 발생했습니다")

    return app


app = create_app(persistence=Persistence())
