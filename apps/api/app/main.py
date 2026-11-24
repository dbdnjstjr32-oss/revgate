"""RevGate API application (blueprint §4: REST + SSE)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text

from .config import get_settings
from .db import make_engine, make_sessionmaker
from .errors import ApiError, api_error_handler
from .queue import make_pool
from .routers import auth, dev, diffs, manufacturing, parts, public, review, shop, workspace
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = make_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.arq = await make_pool(settings.redis_url)
    app.state.storage = Storage(settings)
    try:
        yield
    finally:
        await app.state.arq.aclose()
        await app.state.redis.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="RevGate API", version="0.4.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.web_base_url], allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["*"])
    app.add_exception_handler(ApiError, api_error_handler)
    for r in (auth.router, workspace.router, parts.router, manufacturing.router, diffs.router, public.router, review.router,
              shop.router):
        app.include_router(r)
    if settings.env != "prod" and settings.dev_outbox:
        app.include_router(dev.router)  # local-development hook only

    @app.get("/health", tags=["ops"])
    async def health(request: Request):
        st = request.app.state
        checks: dict[str, str] = {}
        try:
            async with st.engine.connect() as conn:
                checks["db"] = "ok" if (await conn.execute(text("SELECT 1"))).scalar() == 1 else "fail"
        except Exception as exc:  # noqa: BLE001
            checks["db"] = f"fail: {exc.__class__.__name__}"
        try:
            checks["redis"] = "ok" if await st.redis.ping() else "fail"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"fail: {exc.__class__.__name__}"
        try:
            checks["s3"] = "ok" if await st.storage.ping() else "fail"
        except Exception as exc:  # noqa: BLE001
            checks["s3"] = f"fail: {exc.__class__.__name__}"
        ok = all(v == "ok" for v in checks.values())
        from fastapi.responses import JSONResponse

        return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)

    return app


app = create_app()
