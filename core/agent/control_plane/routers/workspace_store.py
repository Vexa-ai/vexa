"""routers/workspace_store.py — the two workspace-store routes a worker uses (WS-S3).

``GET /api/workspace-store/fetch`` and ``POST /api/workspace-store/push``, published by the gateway
as ``/agent/workspace-store/*`` for a worker's own delegation token (``routes.v1.json``,
``delegation``). On the volume backend both answer 404 ``volume_backend``: the worker's workspaces
are bind mounts and there is nothing to fetch. On the s3 backend a worker fetches and pushes only
the mounts its own dispatch recorded (``workspace_store_service.record_unit_mounts``), pushes only
writable ones, fast-forward only, never touching ``policy/``, ``.vexa/``, ``.claude/`` or ``.git``
in any case; a push answers 200 only once the new generation is durable in the bucket.

A PUSH IS HELD TO THE PRIVATE-WRITE RULE HERE (D6, SC4). Every file each pushed commit writes passes
``private_taint.check_push`` — the page route's hold, on the workspace's authoritative roster (the
policy store, never the tree's mirror) — before it lands: a session that read the person's private
data cannot carry it into a workspace others read by committing it itself. A held push answers 409
``private_sources_to_shared`` and nothing lands; a roster that cannot be read answers 503
``policy_unreadable`` and nothing lands.
"""
from __future__ import annotations

import os

from control_plane import private_taint
from control_plane import workspace_store_service as svc
from control_plane.workspace_membership import POLICY_UNREADABLE, MembershipError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

_PUSH_MAX_BYTES = 256 * 1024 * 1024


def build(**d) -> APIRouter:
    router = APIRouter()
    settings = d.get("settings")
    wsr = d.get("wsr")
    redis_url = d.get("redis_url") or ""
    _client = {}

    def _redis():
        if not redis_url:
            return None
        if "c" not in _client:
            import redis  # noqa: PLC0415

            _client["c"] = redis.from_url(redis_url, decode_responses=True,
                                          socket_connect_timeout=2, socket_timeout=5)
        return _client["c"]

    def _run():
        run = svc.running()
        if run is None:
            raise HTTPException(status_code=404, detail={"refused": "volume_backend",
                                                        "message": "Workspaces are on a volume here."})
        return run

    def _authorized(request: Request, run, path: str, *, write: bool) -> str:
        """The mount ``path`` as a store-relative repository, if this request's unit may use it."""
        unit = (request.headers.get(svc.UNIT_HEADER) or "").strip()
        subject = (request.headers.get("x-user-id") or "").strip()
        record = svc.unit_record(_redis(), unit) if unit else None
        if not record or not subject or record.get("subject") != subject:
            raise HTTPException(status_code=403, detail={"refused": "not_this_dispatch",
                                                        "message": "This worker has no workspace store grant."})
        mount = next((m for m in record.get("mounts") or [] if m.get("path") == path), None)
        if mount is None:
            raise HTTPException(status_code=403, detail={"refused": "not_mounted",
                                                        "message": "That workspace is not mounted for this dispatch."})
        if write and not mount.get("write"):
            raise HTTPException(status_code=403, detail={"refused": "read_only",
                                                        "message": "That workspace is mounted read-only."})
        root = os.path.realpath(str(run.store.cache_root))
        real = os.path.normpath(os.path.join(root, os.path.relpath(path, settings.workspaces_dir))) \
            if settings is not None else os.path.normpath(path)
        if not real.startswith(root.rstrip("/") + "/"):
            raise HTTPException(status_code=403, detail={"refused": "outside_store",
                                                        "message": "That path is outside the workspace store."})
        return real[len(root.rstrip("/")) + 1:]

    def _policy_root() -> str:
        """The store root the membership policy lives under — the one every access decision reads."""
        if wsr is not None:
            return str(wsr.root)
        return str(settings.workspaces_dir)

    @router.get("/api/workspace-store/fetch")
    def ws_store_fetch(request: Request, path: str, have: str = ""):
        from workspaces.store.observer import classify  # noqa: PLC0415

        run = _run()
        rel = _authorized(request, run, path, write=False)
        store = run.store
        try:
            with store.locks.hold(rel):
                store.ensure_fresh(rel)
                data = store.bundle_for(rel, have=have.strip())
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — every store fault is typed
            raise classify(exc) from exc
        if data is None:
            return Response(status_code=204)
        return Response(content=data, media_type="application/octet-stream")

    @router.post("/api/workspace-store/push")
    async def ws_store_push(request: Request, path: str, base: str):
        import anyio  # noqa: PLC0415

        from workspaces.store.observer import classify  # noqa: PLC0415

        run = _run()
        rel = _authorized(request, run, path, write=True)
        data = await request.body()
        if not data or len(data) > _PUSH_MAX_BYTES:
            raise HTTPException(status_code=400, detail={"refused": "bad_bundle",
                                                        "message": "The push carries no bundle, or one too large."})
        store = run.store
        unit = (request.headers.get(svc.UNIT_HEADER) or "").strip()
        subject = (request.headers.get("x-user-id") or "").strip()

        def admit(repo, old: str, new: str) -> None:
            try:
                withheld = private_taint.check_push(subject=subject, unit=unit, workspace=os.path.basename(rel),
                                                    root=_policy_root(), ws_dir=os.path.join(_policy_root(), rel),
                                                    repo=repo, base=old, new=new)
            except MembershipError as exc:
                raise HTTPException(status_code=503, detail={
                    "refused": POLICY_UNREADABLE,
                    "message": "Who reads this workspace could not be checked, so nothing was pushed."}) from exc
            if withheld is not None:
                raise HTTPException(status_code=409, detail=withheld)

        def land():
            with store.locks.hold(rel) as held:
                store.ensure_fresh(rel)
                return store.accept_push(rel, data, base=base.strip(), held=held, admit=admit)

        try:
            gen = await anyio.to_thread.run_sync(land)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — every store fault is typed
            raise classify(exc) from exc
        return {"gen": gen.gen, "head": gen.refs.get(gen.head, ""), "durable": True}

    return router
