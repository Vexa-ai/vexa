"""routers/forget.py — `workspace_forget`: take an item out of everything Vexa keeps (Vexa-ai/vexa#1783).

Founder, app.dev 2026-10-13, after an agent read an uploaded power of attorney: *"delete information
about that — it's unnecessary footprint in this knowledge"*, and *"agent should own knowledge git"*.
Deleting the file would have left it in the workspace's history, in every stored version of the
chat's transcript, and in the recently-opened list. A forget reaches all of them:

* the workspace — files removed from the tree AND the history rewritten so no ref reaches them
  (`git_forget.py`); text matches replaced with `[removed]` in every version of every file;
* the stored transcripts of the person's chats — every version, the titles and posted notes, the
  matched spans redacted and each turn kept (`SessionStore.redact_owned`, and any `.claude/` files
  still in the old layout — the desk, `_system`, and the archive `session_migrate --retire` keeps);
* the recently-opened list (`TouchLog.forget`);
* an audit line in the person's private `_system` workspace — what kind of thing, how many files and
  commits, who asked — and never the content.

Two calls, always: the first answers what WOULD go (changing nothing) and the sentence to confirm
with; the second, `confirm: true`, does it. A workspace mirrored to a remote repository is rewritten
here only; the remote keeps its copy until the person says yes to the force-push (`push: true`).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request

from control_plane import git_forget
from control_plane import system_mounts
from control_plane import workspace_credentials as wcreds
from control_plane import workspace_membership as membership_mod
from control_plane.bodies import WorkspaceForgetBody
from control_plane.ceiling import DESK_ALIASES, is_unwatched
from control_plane.workspace_git_sync import RemoteSyncError, home_remote, push_origin
from control_plane.workspace_membership import MembershipError
from shared import sensitive
from shared.gitexec import run_git
from shared.git_redaction import redact as redact_secrets
from workspaces.shared import workspace_paths as wpaths

#: What cannot be reached from here, said every time so nobody believes otherwise.
OUT_OF_REACH = ("copies already sent elsewhere (email, chat apps, downloads) and anything a "
                "person or a remote repository already fetched")


def build(**d) -> APIRouter:
    router = APIRouter()
    _read_target = d["_read_target"]
    _workspace_key = d["_workspace_key"]
    session_store = d["session_store"]
    subject_of = d["subject_of"]
    workspace_registry = d["workspace_registry"]
    workspace_touches = d["workspace_touches"]
    wsr = d["wsr"]

    def _paths(raw: list[str]) -> list[str]:
        out = []
        for p in raw or []:
            rel = str(p or "").strip().lstrip("./").strip("/")
            if not rel:
                continue
            try:
                wpaths.relative_parts(rel)
            except wpaths.PathRefused:
                raise HTTPException(status_code=400, detail=f"not a workspace path: {rel!r}")
            out.append(rel)
        return out

    def _item_values(ws: Path, paths: list[str]) -> list[str]:
        """The identifiers the forgotten files themselves carry — so the same values are found in a
        title, a note or a transcript turn where their keyword is not beside them."""
        vals: set = set()
        for rel in paths:
            raw = None
            try:
                raw = wpaths.read_bytes_inside(ws, rel, max_bytes=25 * 1024 * 1024)
            except Exception:  # noqa: BLE001
                raw = None
            if raw is None and (ws / ".git").is_dir():
                # NOT ON DISK: its last version in history — the newest commit that added or changed
                # it (a removal commit has no copy, and a file deleted without one is still in HEAD)
                r = run_git(ws, "log", "--all", "--format=%H", "-1", "--diff-filter=AMR", "--", rel,
                            check=False)
                sha = (r.stdout or "").strip()
                if sha:
                    shown = run_git(ws, "show", f"{sha}:{rel}", check=False, text=False)
                    raw = shown.stdout if shown.returncode == 0 else None
            if raw:
                vals.update(sensitive.values(sensitive.document_text(raw, rel)))
        return sorted(vals)

    def _in_history(ws: Path, paths: list[str]) -> tuple[list[str], int]:
        if not paths or not (ws / ".git").is_dir():
            return [], 0
        names = run_git(ws, "log", "--all", "--name-only", "--format=", "--", *paths, check=False).stdout
        commits = run_git(ws, "rev-list", "--all", "--count", "--", *paths, check=False).stdout.strip()
        return sorted({n for n in names.splitlines() if n.strip()}), int(commits or 0)

    def _redact_continuity(ws: Path, red) -> int:
        """Store off: the transcripts are the `.claude/` files in the person's own workspace."""
        n = 0
        try:
            rels = list(wpaths.walk_files_inside(ws, ".claude", allow=(".claude",)))
        except (wpaths.PathRefused, OSError):
            return 0
        for rel in rels:
            rel = str(rel)
            try:
                text = wpaths.read_text_inside(ws, rel, max_bytes=50_000_000, allow=(".claude",))
            except (wpaths.PathRefused, OSError, UnicodeDecodeError):
                continue
            if text is None:
                continue
            new, k = red(text)
            if k:
                wpaths.write_text_inside(ws, rel, new, allow=(".claude",))
                n += k
        return n

    def _audit(subject: str, entry: dict) -> None:
        store = system_mounts.system_store_path(wsr.root, subject)
        try:
            system_mounts.ensure_system_workspace(str(wsr.root), subject)
            rel = "audit/forget.jsonl"
            before = wpaths.read_text_inside(store, rel) or ""
            wpaths.write_text_inside(store, rel, before + json.dumps(entry, sort_keys=True) + "\n")
            run_git(store, "-c", "user.name=Vexa", "-c", "user.email=platform@vexa.ai", "add", "--",
                    rel, check=False)
            run_git(store, "-c", "user.name=Vexa", "-c", "user.email=platform@vexa.ai", "commit", "-q",
                    "--no-verify", "-m", "audit: forget", "--", rel, check=False)
        except Exception:  # noqa: BLE001 — the audit is a record, not a gate; the act is done
            pass

    @router.post("/api/workspace/forget")
    def ws_forget(request: Request, body: WorkspaceForgetBody = Body(...)):
        """FORGET something everywhere Vexa keeps it — the person said "delete that", "forget this",
        "remove it from my knowledge". Two calls, always:

        1. without `confirm`: nothing changes; the answer says what would go (files, how many commits
           of history, whether a remote repository also holds it) and the one-line question to ask.
           Ask it, in one line.
        2. with `confirm: true`, after they said yes: the files leave the workspace AND its git
           history (rewritten, old copies pruned), the matches are redacted in every version of every
           page and in the stored transcripts of their chats (turns kept, spans `[removed]`), and the
           recently-opened list forgets the files. Then say plainly what was removed and what could
           not be (copies already sent by email, a remote repository unless `push` was agreed).

        An attachment is its `uploads/…` path. Pass `identifiers: true` to also remove government ids,
        passport, card and account numbers and credentials. Never repeat a removed value."""
        subject = str(subject_of(request))
        paths = _paths(body.paths)
        slug = (body.slug or "").strip() or None
        if slug in DESK_ALIASES:
            slug = None
        ws = _read_target(request, slug, write=True)
        if slug and slug != subject:
            try:
                if membership_mod.is_member(wsr.root, slug, subject) is not None:
                    membership_mod.require_role(wsr.root, slug, subject, "owner")
            except MembershipError as exc:
                raise HTTPException(status_code=exc.status, detail="only an owner rewrites a shared "
                                    "workspace's history")
        literals = _item_values(ws, paths)
        red = sensitive.redactor(literals=literals, pattern=body.text, identifiers=body.identifiers)
        if not paths and not red.active:
            raise HTTPException(status_code=400, detail="name what to forget: paths, text or identifiers")
        name = slug or "your personal workspace"
        on_disk = [p for p in paths if (ws / p).exists()]
        hist_names, hist_commits = _in_history(ws, paths)
        home = home_remote(ws) if (ws / ".git").is_dir() else None
        # A NAMED FILE ALREADY ABSENT is reported, never an error: removed earlier (its copies may still
        # be in history and transcripts, which this reaches) or never kept here at all.
        hist_set = set(hist_names)
        gone_from_disk = [p for p in paths if p not in on_disk and (p in hist_set or any(
            git_forget.matches_path(n, [p]) for n in hist_names))]
        gone = [p for p in paths if p not in on_disk and p not in gone_from_disk]
        what = []
        if paths and (on_disk or hist_names):
            what.append(f"{len(set(on_disk) | hist_set) or len(paths)} file(s)")
        if body.text.strip():
            what.append("a phrase you named")
        if body.identifiers or literals:
            what.append("the identifiers it carries")
        said_what = " and ".join(what) or "it"
        gone_line = (f" Already gone, nothing kept here: {', '.join(gone)}." if gone else "")
        if not body.confirm:
            return {
                "changed": False,
                "would_forget": {"files": sorted(set(on_disk) | set(hist_names)),
                                 "history_commits_touching_them": hist_commits,
                                 "already_gone_from_disk": gone_from_disk,
                                 "already_gone": gone,
                                 "text": bool(body.text.strip()),
                                 "identifiers": bool(body.identifiers or literals),
                                 "transcripts": "every stored version of your chats is searched",
                                 "recently_opened": True},
                "remote": ({"repository": home[1].split("@")[-1], "keeps_its_copy": True,
                            "force_push_needed": True} if home else None),
                "ask": ((f"Forget {said_what} from {name} — files, git history and chat transcripts? "
                         "This rewrites history and cannot be undone."
                         + (" The connected repository keeps its copy unless you also want a "
                            "force-push." if home else "")) if (what or not gone) else
                        f"Nothing named is kept in {name}: {', '.join(gone)} is already gone.")
                       + (gone_line if what else ""),
            }
        if is_unwatched(request):
            raise HTTPException(status_code=403, detail="a forget needs the person in the chat")
        try:
            out = git_forget.forget(ws, paths=paths, pattern=body.text or None,
                                    identifiers=body.identifiers, literals=literals,
                                    author=(subject, request.headers.get("x-user-email") or
                                            f"{subject}@vexa.local"))
        except git_forget.ForgetRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))
        transcripts = {"sessions": 0, "spans": 0}
        if red.active:
            # BOTH homes a transcript can have: the session store (every version), and the desk's own
            # `.claude/` files, which a store-off deployment uses and a store-on one may still hold.
            if session_store is not None:
                transcripts = dict(session_store.redact_owned(subject, red))
            try:
                own = wsr.workspace_dir(subject)
                transcripts["spans"] = int(transcripts.get("spans") or 0) + _redact_continuity(Path(own), red)
            except ValueError:
                pass
            # ...and the old layout's other homes: the `_system` tier, and the copies
            # `session_migrate --retire` moved out of every mount (kept for the operator, never lost).
            from control_plane import session_migrate  # noqa: PLC0415
            for extra in [system_mounts.system_store_path(wsr.root, subject),
                          *session_migrate.archived_roots(wsr.root, subject)]:
                transcripts["spans"] = int(transcripts.get("spans") or 0) + _redact_continuity(Path(extra), red)
        touched = 0
        if paths:
            desk = workspace_registry.by_slug(subject)
            if desk and desk.get("id"):
                ws_rec = workspace_registry.by_slug(slug or subject) or {}
                wid = str(ws_rec.get("id") or "")
                touched = workspace_touches.forget(
                    desk["id"], lambda w, p: (not wid or w == wid) and git_forget.matches_path(p, paths))
        pushed = None
        not_removed = [OUT_OF_REACH]
        if home:
            if body.push:
                try:
                    key = _workspace_key(subject, slug)
                    with wcreds.for_workspace(wsr.root, key=key, repo_url=home[1], subject=subject,
                                              explicit_token=None) as cred:
                        if cred.kind == "none":
                            raise RemoteSyncError("this workspace has no credential for its repository")
                        r = push_origin(ws, token=cred.token, ssh_env=cred.ssh_env, force=True)
                    pushed = {"branch": r.branch, "head": r.head_sha}
                except (RemoteSyncError, ValueError) as exc:
                    not_removed.append(f"the connected repository still has it: {redact_secrets(str(exc))}")
            else:
                not_removed.append("the connected repository still has it — not force-pushed")
        result = {
            "changed": True,
            "removed": {"workspace": {**out.as_dict(), "files_removed": len(out.files_removed),
                                      "already_gone_from_disk": gone_from_disk, "already_gone": gone},
                        "transcripts": transcripts, "recently_opened": touched},
            "pushed": pushed,
            "not_removed": not_removed,
            "say": ("Say plainly what was removed, where, and what was not — in a few lines. "
                    "Never repeat a removed value."),
        }
        _audit(subject, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": subject,
                         "workspace": slug or "personal", "kind": [k for k, v in (
                             ("files", bool(paths)), ("text", bool(body.text.strip())),
                             ("identifiers", bool(body.identifiers or literals))) if v],
                         "files": len(out.files_removed), "commits_rewritten": out.commits_changed,
                         "matches": out.matches_redacted, "transcript_spans": transcripts.get("spans"),
                         "pushed": bool(pushed)})
        return result

    return router
