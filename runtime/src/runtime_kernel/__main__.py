"""``python -m runtime_kernel`` — the production runtime API (P4 compose CMD).

Serves ``runtime_kernel.api.create_app(Runtime(backend=DockerBackend(), profiles=default_registry()))``
— the runtime.v1 operation surface that spawns bot/agent containers via the host Docker socket. The
``DockerBackend`` shells out to the ``docker`` CLI, so the image bundles the docker client and the
compose mounts ``/var/run/docker.sock``. Images come from env (BROWSER_IMAGE / AGENT_IMAGE).

Exposed via ``app`` (PEP 562, built on first access) so ``uvicorn runtime_kernel.api:app`` /
``python -m runtime_kernel`` both resolve it without constructing the app at mere import time.
"""
from __future__ import annotations

import os


def build_production_app():
    """Wire the runtime API with the real Docker backend + the env-driven profile registry."""
    from .api import create_app
    from .docker_backend import DockerBackend
    from .kernel import Runtime
    from .profiles import default_registry

    return create_app(Runtime(backend=DockerBackend(), profiles=default_registry()))


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_production_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8090")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
