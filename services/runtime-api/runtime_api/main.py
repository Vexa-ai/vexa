"""FastAPI application — container lifecycle API.

Startup: connects Redis, initializes the configured backend, reconciles state,
starts the idle management loop and event listener.

Shutdown: cancels background tasks, closes connections.
"""

from __future__ import annotations

import asyncio
import logging
import os

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from runtime_api import config
from runtime_api.api import router
from runtime_api.lifecycle import handle_container_exit, idle_loop, reconcile_state
from runtime_api.profiles import install_sighup_handler, load_profiles
from runtime_api.scheduler import start_executor, stop_executor
from runtime_api.scheduler_api import scheduler_router

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("runtime_api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Container Lifecycle API",
        description="Generic container orchestration with pluggable backends",
        version="0.1.0",
    )

    # CORS
    cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key auth middleware (optional — disabled when API_KEYS is empty)
    if config.API_KEYS:
        app.add_middleware(APIKeyMiddleware)

    app.include_router(router)
    app.include_router(scheduler_router)

    @app.on_event("startup")
    async def startup():
        # Redis
        app.state.redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
        await app.state.redis.ping()
        logger.info("Redis connected")

        # Load profiles
        load_profiles()
        install_sighup_handler()

        # Initialize backend
        backend = _create_backend()
        app.state.backend = backend
        await backend.startup()
        logger.info(f"Backend '{config.ORCHESTRATOR_BACKEND}' initialized")

        # Process backend needs Redis reference
        if config.ORCHESTRATOR_BACKEND == "process":
            backend.set_redis(app.state.redis)

        # Reconcile state with backend reality
        await reconcile_state(app.state.redis, backend)

        # Start event listener for exit detection
        async def on_exit(name: str, exit_code: int):
            await handle_container_exit(app.state.redis, name, exit_code)
            try:
                await backend.remove(name)
            except Exception:
                logger.warning(f"Failed to remove exited container {name}", exc_info=True)

        await backend.listen_events(on_exit)

        # Start idle management loop
        app.state.idle_task = asyncio.create_task(idle_loop(app.state.redis, backend))

        # Start scheduler executor
        app.state.scheduler_task = asyncio.create_task(start_executor(app.state.redis))
        logger.info("Runtime API ready")

    @app.on_event("shutdown")
    async def shutdown():
        if hasattr(app.state, "idle_task"):
            app.state.idle_task.cancel()
        if hasattr(app.state, "scheduler_task"):
            await stop_executor()
            app.state.scheduler_task.cancel()
        if hasattr(app.state, "backend"):
            await app.state.backend.shutdown()
        if hasattr(app.state, "redis"):
            await app.state.redis.close()

    return app


def _create_backend():
    """Create the configured backend instance."""
    backend_name = config.ORCHESTRATOR_BACKEND.lower()

    if backend_name == "docker":
        from runtime_api.backends.docker import DockerBackend
        return DockerBackend()
    elif backend_name == "kubernetes":
        from runtime_api.backends.kubernetes import KubernetesBackend
        return KubernetesBackend()
    elif backend_name == "process":
        from runtime_api.backends.process import ProcessBackend
        return ProcessBackend()
    else:
        raise ValueError(f"Unknown backend: {backend_name}. Use: docker, kubernetes, process")


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Simple API key validation from X-API-Key header."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health endpoint
        if request.url.path in ("/health", "/docs", "/openapi.json"):
            return await call_next(request)

        # Skip auth if no API keys configured (dev mode)
        if not config.API_KEYS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "")
        if api_key not in config.API_KEYS:
            return JSONResponse(status_code=403, content={"detail": "Missing API token (X-API-Key header)"})

        return await call_next(request)


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
