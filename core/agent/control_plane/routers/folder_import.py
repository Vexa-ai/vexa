"""routers/folder_import.py — the four doors a dropped folder goes through (`control_plane/folder_import.py`
holds the rules).

    POST /api/workspace/folder/plan                 the manifest in, the plan out (what is imported,
                                                    what is skipped and why, the caps, a refusal)
    POST /api/workspace/folder/{plan_id}/files      one batch of the accepted files' bytes
    POST /api/workspace/folder/{plan_id}/finish     a new workspace (the existing create path) or a
                                                    sub-folder of one the person may write; ONE commit
                                                    authored by the person
    GET  /api/workspace/folder/{plan_id}/status     the receipt, for the person and for the agent
                                                    (`workspace_folder_import_status`)

Batches, not one body: the gateway buffers each request, so the browser sends the bytes in pieces of a
few megabytes and every piece is re-checked here — path in the plan, per-file cap, running total, and
the content scan for keys and tokens. The three writes need a person (`routes.v1.json` `verbs`); the
subject is the gateway's signed one and a plan is only ever found in its owner's store.

Mounted from `routers/workspaces.py` so the write into an EXISTING workspace is authorized by the
same `_write_dir` every other workspace write uses — one permission rule, never two copies of it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from control_plane import folder_import as fi
from control_plane import system_mounts
from control_plane.api_shared import logger
from control_plane.ceiling import require_in_ceiling
from control_plane.workspace_attach import create_workspace, workspace_slot_dir
from shared.gitexec import run_git
from workspaces.shared import workspace_paths as wpaths

#: One batch's ceiling, whatever the browser sends: well under any proxy's body limit.
MAX_BATCH_BYTES = 64 * 1024 * 1024
MAX_BATCH_FILES = 1000
_CHUNK = 1024 * 1024


class FolderPlanBody(BaseModel):
    name: str = Field("", max_length=200)
    files: list[dict] = Field(default_factory=list)
    gitignores: dict[str, str] = Field(default_factory=dict)
    pruned: list[str] = Field(default_factory=list)
    #: An EXISTING workspace to add the folder into (as the sub-folder `into`); absent = a new one.
    slug: Optional[str] = Field(None, max_length=128)
    into: str = Field("", max_length=200)


class FolderFinishBody(BaseModel):
    pass


def mount(router: APIRouter, *, subject_of: Callable, wsr, settings, write_dir: Callable) -> None:

    def _global_dir() -> Path:
        return system_mounts.global_root(settings, wsr.root)

    def _limits() -> dict:
        try:
            return fi.read_limits(_global_dir())
        except fi.PolicyUnreadable as exc:
            raise HTTPException(status_code=503, detail=(
                f"folder import is refused until _global/POLICIES.md is fixed: {exc.key} {exc.why}"))

    def _record(request: Request, plan_id: str) -> tuple[str, dict]:
        subject = subject_of(request)
        try:
            rec = fi.load(wsr.root, subject, plan_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown folder import")
        return subject, rec

    def _existing_target(request: Request, subject: str, target: dict) -> Path:
        slug = (target.get("slug") or "").strip()
        if slug in (system_mounts.GLOBAL_SLUG, system_mounts.SYSTEM_SLUG) or not slug:
            raise HTTPException(status_code=400, detail="a folder is imported into a workspace, not into "
                                                        "the company layer or your private settings")
        return write_dir(request, subject, target.get("into") or "imported", slug)

    @router.post("/api/workspace/folder/plan")
    def folder_plan(request: Request, body: FolderPlanBody):
        """Decide what of a dropped folder is imported. Send relative paths and sizes (and the text of
        every .gitignore); get back the accepted files, the skipped ones with a reason, the caps and,
        when the folder cannot be imported at all, `blocked`. Nothing is written to a workspace."""
        subject = subject_of(request)
        if len(body.files) > fi.MAX_MANIFEST:
            raise HTTPException(status_code=413, detail=f"more than {fi.MAX_MANIFEST} files listed")
        limits = _limits()
        target = None
        if body.slug is not None and body.slug.strip():
            require_in_ceiling(request, body.slug.strip())
            target = {"slug": body.slug.strip(), "into": fi.safe_folder_name(body.into or body.name)}
            _existing_target(request, subject, target)
        plan = fi.build_plan(body.files, body.gitignores, limits, body.pruned)
        name = " ".join(str(body.name or "").split())[:80] or "Imported folder"
        try:
            plan_id = fi.new_id()
            rec = {"plan_id": plan_id, "status": "blocked" if plan.blocked else "planned", "name": name,
                   "target": target, "accepted": {a["path"]: a["size"] for a in plan.accepted},
                   "received": {}, "skipped": list(plan.skipped), "counts": dict(plan.counts),
                   "total_bytes": plan.total_bytes, "blocked": plan.blocked, "limits": fi.limits_view(limits)}
            fi.save(wsr.root, subject, rec)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        return {"plan_id": plan_id, "name": name, "target": target, "accepted": plan.accepted,
                "skipped": plan.skipped, "counts": plan.counts, "total_bytes": plan.total_bytes,
                "files": len(plan.accepted), "blocked": plan.blocked, "limits": fi.limits_view(limits)}

    @router.post("/api/workspace/folder/{plan_id}/files")
    async def folder_files(plan_id: str, request: Request):
        """One batch of an accepted plan's bytes: `files` parts with a parallel `paths` field."""
        subject, rec = _record(request, plan_id)
        if rec["status"] != "planned":
            raise HTTPException(status_code=409, detail=rec.get("blocked") or f"this import is {rec['status']}")
        form = await request.form(max_files=MAX_BATCH_FILES, max_fields=MAX_BATCH_FILES + 10)
        uploads = form.getlist("files")
        paths = [str(p) for p in form.getlist("paths")]
        if len(paths) != len(uploads):
            raise HTTPException(status_code=400, detail="each file needs its path")
        lim = rec["limits"]
        received: dict = rec["received"]
        total = sum(received.values())
        stage = fi.staging(wsr.root, subject, plan_id)
        took, skipped = [], []
        batch = 0
        try:
            for rel_raw, up in zip(paths, uploads):
                rel = fi._norm(rel_raw)
                reason = fi.skip_reason(rel)
                if reason is None and rel not in rec["accepted"]:
                    reason = "not in the plan"
                content = b""
                if reason is None:
                    cap = lim["file_max_bytes"]
                    chunks, n = [], 0
                    while True:
                        piece = await up.read(_CHUNK)
                        if not piece:
                            break
                        n += len(piece)
                        if n > cap:
                            reason = f"larger than {fi._mb(cap)}"
                            break
                        chunks.append(piece)
                    content = b"".join(chunks)
                if reason is None:
                    batch += len(content)
                    if batch > MAX_BATCH_BYTES:
                        raise HTTPException(status_code=413, detail="send the files in smaller batches")
                    if total - received.get(rel, 0) + len(content) > lim["max_bytes"]:
                        reason = f"over the {fi._mb(lim['max_bytes'])} import limit"
                if reason is None:
                    reason = fi.scan_secret(content)
                if reason:
                    skipped.append({"path": rel or rel_raw, "reason": reason})
                    continue
                try:
                    wpaths.write_bytes_inside(stage, rel, content, mode=0o600)
                except wpaths.PathRefused:
                    skipped.append({"path": rel, "reason": fi.REASON_PATH})
                    continue
                total += len(content) - received.get(rel, 0)
                received[rel] = len(content)
                took.append(rel)
        finally:
            for up in uploads:
                try:
                    await up.close()
                except Exception:  # noqa: BLE001
                    pass
        for s in skipped:
            rec["skipped"].append(s)
            rec["counts"][s["reason"]] = rec["counts"].get(s["reason"], 0) + 1
            if s["reason"] != "not in the plan":   # refused at upload: no longer expected at finish
                rec["accepted"].pop(s["path"], None)
                rec["received"].pop(s["path"], None)
        fi.save(wsr.root, subject, rec)
        return {"received": took, "skipped": skipped, "received_total": len(received),
                "expected_total": len(rec["accepted"]), "bytes": total}

    @router.post("/api/workspace/folder/{plan_id}/finish", status_code=201)
    def folder_finish(plan_id: str, request: Request,
                      body: FolderFinishBody = Body(default=FolderFinishBody())):
        """Write the received files: a NEW private workspace named after the folder (the existing
        create path), or the sub-folder of an existing workspace the plan named. One commit, authored
        by the person. Files the plan accepted but that never arrived are listed as skipped."""
        subject, rec = _record(request, plan_id)
        if rec["status"] == "completed":
            return rec["result"]
        if rec["status"] != "planned":
            raise HTTPException(status_code=409, detail=rec.get("blocked") or f"this import is {rec['status']}")
        received = [p for p in rec["accepted"] if p in rec["received"]]
        missing = [p for p in rec["accepted"] if p not in rec["received"]]
        if not received:
            raise HTTPException(status_code=409, detail="no files were received for this import")
        stage = fi.staging(wsr.root, subject, plan_id)
        target = rec.get("target")
        if target:
            require_in_ceiling(request, target["slug"])
            base = _existing_target(request, subject, target)
            into = fi.safe_folder_name(target.get("into"))
            folder, n = into, 1
            while wpaths.stat_inside(base, folder) is not None:
                n += 1
                folder = f"{into}-{n}"
            slug = target["slug"]
        else:
            try:
                created = create_workspace(wsr.root, subject, name=rec["name"])
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid subject")
            slug = created.slug
            base = workspace_slot_dir(wsr.root, subject, slug)
            folder = ""
        written = []
        for rel in received:
            data = wpaths.read_bytes_inside(stage, rel)
            if data is None:
                missing.append(rel)
                continue
            dest = f"{folder}/{rel}" if folder else rel
            try:
                wpaths.write_bytes_inside(base, dest, data)
            except wpaths.PathRefused:
                missing.append(rel)
                continue
            written.append(dest)
        commit = _commit_as_person(request, base, written, rec["name"])
        skipped = rec["skipped"] + [{"path": p, "reason": fi.REASON_NOT_RECEIVED} for p in missing]
        result = {"slug": slug, "workspace": slug, "name": rec["name"], "path": folder,
                  "files": len(written), "bytes": sum(rec["received"].get(p, 0) for p in received),
                  "skipped": skipped[: fi.SKIPPED_LISTED], "skipped_total": len(skipped),
                  "commit": commit, "new": not target}
        rec.update(status="completed", result=result)
        fi.save(wsr.root, subject, rec)
        fi.drop_staging(wsr.root, subject, plan_id)
        logger.info("folder import: subject=%s slug=%s files=%d skipped=%d", subject, slug,
                    len(written), len(skipped))
        return result

    @router.get("/api/workspace/folder/{plan_id}/status")
    def folder_status(plan_id: str, request: Request):
        """Read a folder import the person started by dropping a folder: planned (bytes arriving),
        blocked (with the reason), or completed with the workspace it made and what was skipped."""
        _, rec = _record(request, plan_id)
        out = {"plan_id": plan_id, "status": rec["status"], "name": rec["name"], "target": rec.get("target"),
               "expected": len(rec["accepted"]), "received": len(rec["received"]),
               "counts": rec["counts"], "blocked": rec.get("blocked")}
        if rec.get("result"):
            out["result"] = rec["result"]
        return out

    def _commit_as_person(request: Request, base: Path, paths: list, name: str) -> Optional[str]:
        """ONE commit of exactly the imported paths, AUTHORED by the person (the platform commits, the
        person decided — `workspace_membership.policy_commit_as`, the same split). Staged and committed
        by pathspec, so nothing else in the index rides along."""
        if not paths or not (base / ".git").is_dir():
            return None
        email = (request.headers.get("x-user-email") or "").strip()
        who = (email.split("@", 1)[0] if email else "") or str(subject_of(request))
        author = f"{' '.join(who.split())} <{email or 'platform@vexa.ai'}>"
        spec = "\n".join(paths) + "\n"
        platform = ("-c", "user.name=vexa-platform", "-c", "user.email=platform@vexa.ai")
        run_git(base, "add", "--pathspec-from-file=-", input=spec)
        done = run_git(base, *platform, "commit", "-q", "--author", author,
                       "-m", f"import folder: {name} ({len(paths)} files)", "--pathspec-from-file=-",
                       input=spec)
        if done.returncode != 0:
            logger.warning("folder import: commit failed in %s: %s", base, (done.stderr or "")[:300])
        return run_git(base, "rev-parse", "HEAD").stdout.strip() or None
