"""Cookbook — high-level composed operations over the Slim client.

These are the verbs you actually want: "put an agent on a meeting", "harvest what it produced",
"listen to a meeting end-to-end". Each orchestrates the two domain sub-clients and RETURNS data
(never prints), so they compose into bigger flows, tests, or notebooks. This is the top of the
library: the SDK (`client.py`) is the mechanism; the cookbook is the intent.
"""
from __future__ import annotations

import httpx

from .client import Slim
from .harvest import harvest  # the event-collection mechanism (composed by listen_to_meeting)
from .models import Harvest


# ── identity & connect (bootstrap) — the one INVERTED verb: PRODUCES a bound `slim` ─────────────────-
# The cookbook owns only the VERB; the identity domain owns the mechanism (admin-api mints scoped tokens;
# the gateway resolves X-API-Key → user → X-User-Id, and exposes the read at GET /auth/me). We NEVER
# reimplement token/credential logic — `connect` just drives those endpoints and returns a bound `Slim`.
async def connect(gateway: str, *, api_key: "str | None" = None, email: "str | None" = None,
                  admin_api: "str | None" = None, admin_token: "str | None" = None) -> Slim:
    """JTBD: "log in." The human-level auth entrypoint — INVERTS the cookbook convention: returns a bound
    `Slim` instead of taking one. Two ways in:

    - **Bind a key** — `connect(gw, api_key=…)` binds an existing key and verifies it resolves (`whoami`).
    - **Provision a fresh user** (dev/throwaway) — `connect(gw, email=…, admin_api=…, admin_token=…)` drives
      the identity admin-api to create-or-find the user + mint a scoped token, then binds it.

    The cookbook holds no credential logic: identity mints, the gateway resolves; `connect` only composes.
    """
    if not api_key:
        if not (email and admin_api and admin_token):
            raise ValueError("connect: pass api_key=… OR email=… + admin_api=… + admin_token=…")
        api_key = await _provision_token(admin_api, admin_token, email)
    slim = Slim(gateway, api_key)
    await whoami(slim)  # verify the key resolves (raises on a bad key)
    return slim


async def whoami(slim: Slim) -> dict:
    """JTBD: "who am I connected as?" — the subject the gateway resolves this connection to.
    Reads the identity edge `GET /auth/me` (`{user_id, email, scopes, …}`)."""
    return await slim.auth_me()


async def _provision_token(admin_api: str, admin_token: str, email: str) -> str:
    """Create-or-find a user by email and mint a token (identity admin-api). Dev/throwaway helper behind
    `connect(email=…)` — the admin-api is the identity domain's mint, never reimplemented here."""
    h = {"X-Admin-API-Key": admin_token, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(f"{admin_api}/admin/users", headers=h, json={"email": email})
        if r.status_code in (400, 409):  # already exists → look it up
            r = await c.get(f"{admin_api}/admin/users/email/{email}", headers=h)
        r.raise_for_status()
        user_id = r.json()["id"]
        t = await c.post(f"{admin_api}/admin/users/{user_id}/tokens", headers=h)
        t.raise_for_status()
        return t.json()["token"]


async def agent_on_meeting(slim: Slim, native: str, *, meet_url: "str | None" = None,
                           platform: str = "google_meet") -> dict:
    """Put an agent on a meeting: optionally send a bot (so a transcript flows), then start the copilot
    processor. Returns the start result — the listening agent is now live."""
    if meet_url:
        await slim.meetings.send_bot(native, url=meet_url, platform=platform)
    return await slim.agent.start_processing(native, platform=platform)


async def listen_to_meeting(slim: Slim, native: str, *, seconds: float = 30.0,
                            meet_url: "str | None" = None, platform: str = "google_meet") -> Harvest:
    """The happy path as one call — returns the same `Harvest` whether the meeting is live or finished.

    SOURCE-AGNOSTIC by design: the agent emits ONE envelope (notes/cards), carried live on redis and
    persisted (same shape) to a durable file when finished — so a finished meeting folds to the SAME
    `Harvest` a live one does. This verb folds the live redis stream (the finished-file fold is a
    lower-level mechanism, not a cookbook verb)."""
    await agent_on_meeting(slim, native, meet_url=meet_url, platform=platform)
    out = await harvest(slim, native, seconds=seconds)   # live branch: fold the redis stream
    await slim.agent.stop_processing(native, platform=platform)
    return out


async def meeting_doc(slim: Slim, native: str) -> "str | None":
    """The human-readable meeting markdown the agent maintains (a DERIVED artifact, distinct from the
    deterministic envelope the worker persists)."""
    doc = await slim.agent.read_doc(native)
    return doc.get("content") if doc else None


# ── chat over the workspace ───────────────────────────────────────────────────────────────────────
async def chat(slim: Slim, prompt: str, *, session: "str | None" = None,
               active: "dict | None" = None, files: "list[str] | None" = None) -> str:
    """One chat turn over the user's workspace; returns the reply text.

    Two reference kinds (different seams):
      - `active={"kind":"meeting","meeting":{...}}` → grants the meeting-scoped MCP tool; the agent reads
        the transcript FRESH from meetings' REST `/transcripts` (cross-domain, scoped token).
      - `files=[...]` → workspace-relative paths the agent should `Read` (in-domain, prompt-injected; no
        tool/token — `Read` is already granted).
    Tools are never chosen by the client — fail-closed."""
    return await slim.agent.chat(prompt, session=session, active=active, files=files)


async def onboard(slim: Slim, *, message: str = "onboard me") -> str:
    """Kick off onboarding: one chat turn pointed at the seed's `onboarding.md` playbook, seeded with
    a first user message (`"onboard me"`). Returns the agent's opening reply — the cold-start interview
    question(s) — which the caller surfaces to the user. By the time this returns the agent is already
    engaging: on the very first call there's a live conversation, not an empty workspace.
    Run AFTER `init_workspace` (the playbook + entity scaffold land in the fresh workspace). Continue
    the interview by passing the returned `session` back through `chat(...)`.
    TWO SEAMS: `onboarding.md` must ship in the validated seed template (template-seeding work), and
    `files=` must be wired on chat to inject the playbook Read (the grounded-chat / files= work).
    Lands as intent/scaffold until the chat `files=` seam is implemented (Phase 5)."""
    return await slim.agent.chat(message, files=["onboarding.md"])


# ── workspace: read / browse (wired today) ─────────────────────────────────────────────────────────
async def browse_workspace(slim: Slim, *, hidden: bool = False) -> list:
    """The workspace file tree (the user's durable agent memory / knowledge graph)."""
    return await slim.agent.workspace_tree(hidden=hidden)


async def read_workspace_file(slim: Slim, path: str) -> "str | None":
    """Read one workspace file's content (e.g. 'agents/meeting.md', 'kg/entities/...')."""
    doc = await slim.agent.workspace_file(path)
    return doc.get("content") if doc else None


# ── workspace: lifecycle (SCAFFOLD — needs upstream the control plane does not expose yet) ──────────
# These are the high-level controls we WANT here so the cookbook is the single mental model. Each
# names the upstream seam it requires; wiring them is the workspace-lifecycle work (init/swap).
async def init_workspace(slim: Slim, *, template: "str | None" = None) -> dict:
    """Materialize this subject's workspace from the validated seed template (idempotent). Wired to
    `POST /agent/workspace/init` (the control plane runs the single seed primitive). Run before `onboard()`."""
    return await slim.agent.init_workspace()


async def mount_workspace(slim: Slim, *, repo: "str | None" = None, ref: "str | None" = None,
                          template: "str | None" = None) -> dict:
    """Select which validated workspace/template the NEXT dispatch MOUNTS (the swap). Wired to
    `POST /agent/workspace/swap`; the endpoint returns 501 until the upstream dispatch swap lands (carry
    `VEXA_WORKSPACE_REPO`/`REF` into spawn — the seam exists in dispatch/spawn, not yet surfaced)."""
    return await slim.agent.swap_workspace()


# ── cadence & automate (the proactive engine — a routine compiles to a durable schedule.v1 cron job) ──
# The cadence backbone behind every proactive behavior (morning digest, post-meeting follow-up, …).
async def schedule_routine(slim: Slim, name: str, *, cron: str, prompt: str,
                           run_now: bool = False) -> dict:
    """JTBD: "do this on a schedule without me asking."

    Author a durable routine — the control plane compiles `(name, cron, prompt)` into a schedule.v1 cron
    job whose due-run POSTs a dispatch back to the agent (the runtime owns the durable cron, so it survives
    restarts). `run_now=True` fires one immediate run so the author sees a result without waiting for cron.
    Returns `{routine, job_id, ran_now}`."""
    return await slim.agent.create_routine(name, cron=cron, prompt=prompt, run_now=run_now)


async def list_routines(slim: Slim) -> list:
    """JTBD: "show me what my agent does automatically."

    The user's routines as cards (file + compiled-job state)."""
    return await slim.agent.list_routines()


async def set_routine_enabled(slim: Slim, name: str, enabled: bool) -> dict:
    """JTBD: "pause/resume an automation."

    Flip a routine on/off by name; the control plane updates the routine and reconciles its cron job
    (disabling cancels the job but keeps the routine for later re-enable)."""
    return await slim.agent.set_routine_enabled(name, enabled=enabled)
