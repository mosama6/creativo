import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from creativo_api.errors import AppError
from creativo_api.routes import router
from creativo_common.ids import new_id
from creativo_common.logging import configure_logging
from creativo_common.settings import PlatformSettings, get_settings
from creativo_common.storage import FileStorage
from creativo_db.seed import seed_catalog
from creativo_db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)


def _error(
    status: int, code: str, message: str, request_id: str, details: dict | None = None
) -> JSONResponse:
    payload: dict = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details:
        payload["error"]["details"] = details
    return JSONResponse(status_code=status, content=payload)


def create_app(settings: PlatformSettings | None = None) -> FastAPI:
    configure_logging()
    resolved = settings or get_settings()
    resolved.assert_production_safe()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(resolved.database_url)
        factory = create_session_factory(engine)
        redis = Redis.from_url(resolved.redis_url, decode_responses=True)
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.redis = redis
        app.state.storage = FileStorage(resolved.storage_dir)
        async with factory() as session:
            await seed_catalog(session)
            await session.commit()
        yield
        await redis.aclose()
        await engine.dispose()

    app = FastAPI(title="Creativo", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Creativo-Client", "X-Request-Id"],
    )

    @app.middleware("http")
    async def request_ids(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_id("req")
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error(
            exc.status_code,
            exc.code,
            exc.message,
            getattr(request.state, "request_id", ""),
            exc.details or None,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(
            422,
            "invalid_request",
            "The request body is not valid.",
            getattr(request.state, "request_id", ""),
            {"issues": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled error", extra={"request_id": getattr(request.state, "request_id", "")}
        )
        return _error(
            500, "internal_error", "Something went wrong.", getattr(request.state, "request_id", "")
        )

    app.include_router(router)
    return app
