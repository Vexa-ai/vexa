"""workspace_names.py — a workspace is called what the person sees it called (Vexa-ai/vexa#1783).

The Files pane lists workspaces by their DISPLAY NAME: Personal, biz, "eugenia - dmitry". The agent
listed slugs — `workspace-1`, `workspace-2`, `biz-864aed18` — so when the person said *"share this
one ▶ Eugenia - Dmitry"* the agent answered that no such workspace existed, and it called a repo
slot "your personal workspace" by a slug the person had never seen. Two vocabularies for one list
is a conversation that cannot converge.

Two things here, both from the same rows:

* ``listing`` — every workspace the person has, as the pane names it: ``slug``, ``name``, ``kind``
  (`personal` · `private` · `shared` · `company`), ``role``, and for their own private ones whether
  they are still ``empty`` and which others carry the ``same_name_as`` them. The names follow the
  terminal's rule (`navigatorApi.buildWorkspaces`): the personal workspace is its label or
  "Personal", a private one its label or slug, a shared one its registry name or id.
* ``resolve`` — the slug a REFERENCE names: a slug passes through; otherwise an exact name, then a
  case-insensitive one; two workspaces answering the same name are ``Ambiguous`` (the caller asks
  which), and a reference nothing answers is returned unchanged for the route's own refusal.

``NameResolution`` applies ``resolve`` to every workspace argument a request to agent-api carries
(`slug`, `workspace_id`, `to_slug`, `credential_workspace` — query or JSON body — and the one
`DELETE /api/workspace/{slug}` path), so each tool accepts a name without each route learning to.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode

from control_plane import workspace_attach as attach
from control_plane import workspace_membership as membership_mod
from control_plane.ceiling import DESK_ALIASES
from shared.gitexec import run_git

log = logging.getLogger("agent_api.workspace_names")

ARG_KEYS = ("slug", "workspace_id", "to_slug", "credential_workspace")
PERSONAL_LABEL = "Personal"


class Ambiguous(Exception):
    """Two or more workspaces answer one name."""

    def __init__(self, ref: str, matches: list[dict]):
        self.ref, self.matches = ref, matches
        names = "; ".join(f"`{m['slug']}` ({m['kind']}{', empty' if m.get('empty') else ''})"
                          for m in matches)
        super().__init__(f"{len(matches)} workspaces are called {ref!r}: {names}. Ask the person "
                         f"which one, and pass its slug.")


def _empty(path: Path) -> Optional[bool]:
    """True when a private workspace holds nothing but its seed commit — its own identity file
    (`.vexa/`) does not count as content, nor does the standard icon picked at birth (README
    `glyph:`, committed as `workspace icon: …` by workspace_icon.assign_glyph)."""
    try:
        out = run_git(path, "rev-list", "--count", "--invert-grep", "--grep=^workspace icon: ", "HEAD",
                      "--", ".", ":(exclude).vexa", timeout=5)
    except Exception:  # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip() or "0") <= 1
    except ValueError:
        return None


def listing(root, subject: str, *, registry=None, index_list: Callable = lambda s: [],
            with_emptiness: bool = False) -> list[dict]:
    """Every workspace ``subject`` has, by the names the person sees (see the header)."""
    rows: list[dict] = []
    rootp = Path(root)
    try:
        mounts = attach.active_workspaces(rootp, subject)
        state = attach._load_state(attach._store(rootp, subject))
    except Exception:  # noqa: BLE001 — an unreadable store lists nothing of its own
        mounts, state = [], {"slots": {}}
    seed = attach._seed_slot_slug(state)
    # PERSONAL IS THE DESK AND SAYS "Personal" (Vexa-ai/vexa#1783, founder 2026-10-15: "badges
    # disagree personal = biz — it's misleading"). It used to borrow the name and id of whatever was
    # FIRST in the active set, so a person whose first active workspace was an attached biz clone saw
    # Personal called "biz" with the clone's id, and the clone had no row of its own. The desk's own
    # slot slug (a legacy swapped-in pointer such as `biz-864aed18`) still RESOLVES here (`also`), and
    # is never a name.
    rows.append({"slug": "personal", "name": PERSONAL_LABEL, "kind": "personal", "role": "owner",
                 **({"also": seed} if seed not in (attach.SEED_SLOT, "personal") else {})})
    active = {m.slug for m in mounts}
    for slug, slot in sorted((state.get("slots") or {}).items()):
        if slug in (seed, attach.SEED_SLOT, attach.SEED_BACKUP_SLOT):
            continue
        if not isinstance(slot, dict):
            continue
        row = {"slug": slug, "name": (slot.get("name") or "").strip() or slug, "kind": "private",
               "role": "owner", "active": slug in active}
        if with_emptiness and not slot.get("repo"):
            try:
                row["empty"] = _empty(attach._slug_dir(rootp, subject, state, slug))
            except Exception:  # noqa: BLE001
                pass
        rows.append(row)
    try:
        memberships, _ = membership_mod.reconciled_memberships(rootp, subject, index_list)
    except Exception:  # noqa: BLE001
        memberships = []
    seen = {r["slug"] for r in rows}
    for m in memberships:
        wid = str(m.get("workspace_id") or "")
        if not wid or wid in seen or wid.startswith("_"):
            continue
        rec = None
        try:
            rec = registry.by_slug(wid) if registry is not None else None
        except Exception:  # noqa: BLE001
            rec = None
        name = ((rec or {}).get("name") or m.get("name") or "").strip() or wid
        rows.append({"slug": wid, "name": name, "kind": "shared", "role": m.get("role") or "member"})
        seen.add(wid)
    # EVERY ROW SAYS ITS STATE (Vexa-ai/vexa#1783): active · inactive · hidden. Hidden rows stay in
    # this listing so a hidden workspace can still be NAMED to unhide it; every surface that shows a
    # list (Files, pickers, the agent's listing) drops them unless asked for hidden ones.
    try:
        states = {r["slug"]: r["state"] for r in attach.workspace_states(rootp, subject, memberships)}
    except Exception:  # noqa: BLE001 — a state read never costs the listing
        states = {}
    for r in rows:
        r["state"] = states.get(r["slug"], "active")
    by_name: dict[str, list[str]] = {}
    for r in rows:
        by_name.setdefault(r["name"].casefold(), []).append(r["slug"])
    for r in rows:
        others = [s for s in by_name[r["name"].casefold()] if s != r["slug"]]
        if others:
            r["same_name_as"] = others
    # THE NAME TO SAY when two workspaces share one (Vexa-ai/vexa#1783): whose, slug, and "empty".
    for r in rows:
        if r.get("same_name_as"):
            whose = "shared" if r["kind"] == "shared" else "yours"
            bits = [whose, r["slug"]] + (["empty"] if r.get("empty") else [])
            r["display"] = f"{r['name']} ({' · '.join(bits)})"
        else:
            r["display"] = r["name"]
    return rows


def resolve(rows: list[dict], ref: Optional[str], subject: str = "") -> Optional[str]:
    """The slug ``ref`` names (see the header). Raises ``Ambiguous``."""
    if ref is None:
        return None
    raw = str(ref)
    target = raw.strip()
    if not target or target in DESK_ALIASES or target == subject or target.startswith("_"):
        return ref
    if any(target == r["slug"] or target == r.get("also") for r in rows):
        return ref
    for test in (lambda r: r["name"] == target, lambda r: r["name"].casefold() == target.casefold()):
        hits = [r for r in rows if test(r)]
        if len(hits) == 1:
            return hits[0]["slug"]
        if len(hits) > 1:
            raise Ambiguous(target, hits)
    return ref


class NameResolution:
    """ASGI middleware: every workspace argument on `/api/workspace*` is resolved by ``resolve``."""

    def __init__(self, app, *, rows_for: Callable[[str], list[dict]]):
        self.app, self.rows_for = app, rows_for

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/api/workspace"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        subject = headers.get("x-user-id") or ""
        if not subject:
            return await self.app(scope, receive, send)
        cache: dict = {}

        def rows() -> list[dict]:
            if "rows" not in cache:
                try:
                    cache["rows"] = self.rows_for(subject)
                except Exception as exc:  # noqa: BLE001 — a listing fault resolves nothing
                    log.warning("workspace name listing failed for %s: %s", subject, type(exc).__name__)
                    cache["rows"] = []
            return cache["rows"]

        def fix(value):
            if not isinstance(value, str) or not value.strip():
                return value
            return resolve(rows(), value, subject)

        try:
            # the query
            qs = scope.get("query_string", b"").decode("latin-1")
            if qs and any(f"{k}=" in qs for k in ARG_KEYS):
                pairs = parse_qsl(qs, keep_blank_values=True)
                new = [(k, fix(v) if k in ARG_KEYS else v) for k, v in pairs]
                if new != pairs:
                    scope = {**scope, "query_string": urlencode(new).encode("latin-1")}
            # the one path that carries a workspace
            path = str(scope.get("path", ""))
            parts = path.split("/")
            if scope.get("method") == "DELETE" and len(parts) == 4 and parts[1:3] == ["api", "workspace"]:
                named = unquote(parts[3])
                resolved = fix(named)
                if resolved != named:
                    new_path = f"/api/workspace/{resolved}"
                    scope = {**scope, "path": new_path, "raw_path": f"/api/workspace/{quote(resolved)}".encode()}
            # the body
            if scope.get("method") in ("POST", "PUT", "PATCH") and "json" in headers.get("content-type", ""):
                chunks, more = [], True
                while more:
                    msg = await receive()
                    chunks.append(msg.get("body", b""))
                    more = msg.get("more_body", False)
                body = b"".join(chunks)
                try:
                    data = json.loads(body) if body else None
                except ValueError:
                    data = None
                if isinstance(data, dict) and any(isinstance(data.get(k), str) for k in ARG_KEYS):
                    changed = {k: fix(data[k]) for k in ARG_KEYS if isinstance(data.get(k), str)}
                    if any(changed[k] != data[k] for k in changed):
                        body = json.dumps({**data, **changed}).encode()
                        scope = {**scope, "headers": [(k, v) for k, v in scope["headers"]
                                                      if k.lower() != b"content-length"]
                                 + [(b"content-length", str(len(body)).encode())]}
                sent = {"done": False}

                async def replay():
                    if not sent["done"]:
                        sent["done"] = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()
                return await self.app(scope, replay, send)
        except Ambiguous as amb:
            payload = json.dumps({"detail": str(amb), "matches": amb.matches}).encode()
            await send({"type": "http.response.start", "status": 409,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(payload)).encode())]})
            await send({"type": "http.response.body", "body": payload})
            return
        return await self.app(scope, receive, send)
