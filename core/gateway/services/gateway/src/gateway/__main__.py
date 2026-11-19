"""``python -m gateway`` — the production run entrypoint (P4 compose CMD).

Serves the gateway edge built by ``adapters.build_production_app()`` (real httpx + redis adapters
from env: ADMIN_API_URL / MEETING_API_URL / REDIS_URL). Equivalent to
``uvicorn gateway.adapters:app`` — kept as a module so the image CMD can be a plain
``python -m gateway`` and HOST/PORT are read from env.

Where the deployment runs a flows tier (``VEXA_FLOWS_SERVICE_KEY`` set), the same process also
serves the SERVICE DOOR (``service_door.py``) on its own port, ``VEXA_GATEWAY_SERVICE_DOOR_PORT``
(8002) — never the public one. Both listeners share one loop and stop together.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    if not (os.getenv("VEXA_FLOWS_SERVICE_KEY") or "").strip():
        uvicorn.run("gateway.adapters:app", host=host, port=port, log_level=log_level)
        return

    from .adapters import build_production_app, load_signing_key
    from .service_door import build_door_app, door_port

    edge = build_production_app()
    door = build_door_app(identity_key=load_signing_key(
        os.getenv("VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE", "")))

    class _Server(uvicorn.Server):
        # One process, two listeners: the handlers below stop both, so neither installs its own
        # (the second would replace the first's and leave one listener running).
        @contextlib.contextmanager
        def capture_signals(self):
            yield

        def install_signal_handlers(self) -> None:  # uvicorn < 0.29
            return None

    servers = [_Server(uvicorn.Config(edge, host=host, port=port, log_level=log_level)),
               _Server(uvicorn.Config(door, host=host, port=door_port(), log_level=log_level))]

    async def _serve() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
