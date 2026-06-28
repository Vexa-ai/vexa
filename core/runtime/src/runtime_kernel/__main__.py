"""``python -m runtime_kernel`` — the production runtime API (P4 compose CMD).

Serves ``runtime_kernel.api.create_app(Runtime(backend=<env-selected>, profiles=default_registry()))``
— the runtime.v1 operation surface that spawns bot/agent workloads. The backend is chosen by
``RUNTIME_BACKEND`` (default ``docker``): ``docker`` talks to the host socket API (compose mounts
``/var/run/docker.sock``), ``k8s`` spawns Pods via kubectl under the runtime's ServiceAccount/RBAC
(deploy/helm), ``process`` runs child processes. Images come from env (BROWSER_IMAGE / AGENT_IMAGE).

Exposed via ``app`` (PEP 562, built on first access) so ``uvicorn runtime_kernel.api:app`` /
``python -m runtime_kernel`` both resolve it without constructing the app at mere import time.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("runtime_kernel.dispatch")


def _http_dispatch(request: dict) -> dict:
    """The scheduler's real dispatch: fire the job's HTTP call when due. A 5xx or a connection error
    raises ``DispatchError`` (retryable — the scheduler backs off and retries); a 2xx completes the
    job (a cron job then re-arms); a 4xx is logged and completed WITHOUT retry (a malformed body won't
    fix itself — a recurring routine simply tries again on its next cron tick)."""
    from .scheduler import DispatchError

    body = request.get("body")
    data = None
    if body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
    headers = {"Content-Type": "application/json", **(request.get("headers") or {})}
    req = urllib.request.Request(
        request["url"], data=data, headers=headers, method=request.get("method", "POST"),
    )
    try:
        with urllib.request.urlopen(req, timeout=request.get("timeout", 30)) as r:
            return {"status_code": r.status}
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise DispatchError(f"{request['url']} -> {e.code}") from e
        logger.warning("schedule dispatch %s -> %s (not retried)", request["url"], e.code)
        return {"status_code": e.code, "error": e.reason}
    except urllib.error.URLError as e:  # connection refused / DNS — retryable
        raise DispatchError(f"{request['url']} unreachable: {e.reason}") from e


def _build_scheduler():
    """Construct the durable cron over REDIS_URL, or None when no redis is configured (the API then
    answers 503 on /schedule — honest, P18). Real redis client; SystemClock; the HTTP dispatch above."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    import redis as redis_lib

    from .scheduler import Scheduler

    client = redis_lib.from_url(redis_url, decode_responses=True)
    return Scheduler(client, dispatch=_http_dispatch)


def _start_ticker(scheduler) -> None:
    """Run the scheduler's tick() loop in a daemon thread (a real deployment loops tick on an
    interval; the eval calls tick() explicitly under a FakeClock). Recovers orphans on startup."""
    interval = float(os.getenv("SCHED_TICK_SEC", "5"))
    try:
        recovered = scheduler.recover_orphans()
        if recovered:
            logger.info("scheduler recovered %d orphaned job(s)", recovered)
    except Exception as e:  # noqa: BLE001 — never let startup recovery crash the boot
        logger.warning("scheduler orphan recovery failed: %s", e)

    def _loop() -> None:
        while True:
            try:
                scheduler.tick()
            except Exception as e:  # noqa: BLE001 — a bad tick must not kill the loop
                logger.warning("scheduler tick error: %s", e)
            time.sleep(interval)

    threading.Thread(target=_loop, name="scheduler-tick", daemon=True).start()


def _build_backend():
    """Select the spawn backend from ``RUNTIME_BACKEND`` (default ``docker``). compose/desktop run
    ``docker`` (host socket API); a k8s deployment runs ``k8s`` (spawns Pods via kubectl under the
    runtime's ServiceAccount/RBAC — see deploy/helm runtime RBAC). ``process`` is the no-container
    fallback. Same Backend port across all three, so the runtime.v1 lifecycle is identical."""
    kind = os.getenv("RUNTIME_BACKEND", "docker").strip().lower()
    if kind == "k8s":
        from .k8s_backend import K8sBackend

        # Namespace is injected via the downward API (POD_NAMESPACE); None ⇒ kubectl's current ns.
        return K8sBackend(namespace=os.getenv("POD_NAMESPACE") or None)
    if kind == "process":
        from .process_backend import ProcessBackend

        return ProcessBackend()
    from .docker_backend import DockerBackend

    return DockerBackend()


def build_production_app():
    """Wire the runtime API with the env-selected spawn backend + the env-driven profile registry,
    plus the durable cron scheduler (REDIS_URL) with a background tick loop."""
    from .api import create_app
    from .kernel import Runtime
    from .profiles import apply_command_overrides, default_registry, worker_image_for

    backend = _build_backend()
    # Workers run the agent-api BYTES under a distinct image NAME. With the Docker backend we ensure
    # that name exists as a local TAG ALIAS of AGENT_IMAGE up front (rebuild-free, no pull). On any
    # failure ensure_image_alias returns AGENT_IMAGE, which we then pin as AGENT_WORKER_IMAGE so the
    # registry resolves to it — dispatch falls back to the agent-api image and never breaks. Other
    # backends (k8s/process) pull the worker image from a registry by its full ref, so there is no
    # local alias to make: we set AGENT_WORKER_IMAGE from worker_image_for(AGENT_IMAGE) directly.
    agent_image = os.getenv("AGENT_IMAGE", "")
    if agent_image:
        if hasattr(backend, "ensure_image_alias"):
            try:
                target = worker_image_for(agent_image)
                resolved = backend.ensure_image_alias(target, agent_image)
                os.environ["AGENT_WORKER_IMAGE"] = resolved
            except Exception as e:  # noqa: BLE001 — startup aliasing must never crash the boot
                logger.warning("worker image alias setup failed: %s; using AGENT_IMAGE", e)
                os.environ["AGENT_WORKER_IMAGE"] = agent_image
        else:
            os.environ.setdefault("AGENT_WORKER_IMAGE", worker_image_for(agent_image))

    scheduler = _build_scheduler()
    if scheduler is not None:
        _start_ticker(scheduler)
    # apply_command_overrides is a no-op unless BOT_COMMAND / AGENT_WORKER_COMMAND are set (the
    # process-backend / `lite` case) — docker/k8s keep the image entrypoints unchanged.
    profiles = apply_command_overrides(default_registry())
    return create_app(
        Runtime(backend=backend, profiles=profiles),
        scheduler=scheduler,
    )


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
