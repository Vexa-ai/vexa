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
    from .config_preflight import preflight
    from .kernel import Runtime
    from .profiles import apply_command_overrides, default_registry, worker_image_for

    # config.v1 boot preflight (ADR-0026): validate the declaration against the env — the runtime has
    # no required-explicit keys today, so this logs the capability tri-states (scheduler · bot_spawn ·
    # agent_spawn · model_inference, incl. the credentials-file probe that catches a SET
    # HOST_CLAUDE_CREDENTIALS whose host file is absent) so a deploy's config completeness is visible
    # in the boot log and on /health BEFORE any workload runs. Capabilities never block boot.
    preflight()

    backend = _build_backend()
    # The agent worker is its OWN image (core/agent/worker/Dockerfile — claude-code + node + the
    # `worker` package), NOT a rename of the agent-api image. With the Docker backend we ensure that
    # image is present up front — pulling it when absent, since the socket create API never
    # implicit-pulls and the compose agent-worker service is a build-only profile `up` skips. On
    # failure the worker name is still pinned so a dispatch fails loudly with 'No such image' rather
    # than silently spawning agent-api bytes that die with 'No module named worker'. Other backends
    # (k8s/process) pull by full ref themselves, so we just derive AGENT_WORKER_IMAGE.
    agent_image = os.getenv("AGENT_IMAGE", "")
    if agent_image:
        target = worker_image_for(agent_image)
        if hasattr(backend, "ensure_worker_image"):
            try:
                target = backend.ensure_worker_image(target)
            except Exception as e:  # noqa: BLE001 — startup image ensure must never crash the boot
                logger.warning("worker image ensure failed: %s; keeping %s", e, target)
        os.environ["AGENT_WORKER_IMAGE"] = target

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
