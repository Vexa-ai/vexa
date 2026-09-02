"""Config is a validated contract, delivered by env (P14).

App vars are ``VEXA_*``; validated against this pydantic-settings model at boot, fail-fast.
Secrets are a class (``*_TOKEN`` / ``*_SECRET`` / ``*_KEY``) — held as ``SecretStr`` so they
never land in a log line, a repr, or a golden. The control plane reads these once at startup.
"""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """The agent-api boot config. Every field arrives by ``VEXA_*`` env (12-factor)."""

    model_config = SettingsConfigDict(env_prefix="VEXA_", extra="ignore")

    # ── Where this service lives ─────────────────────────────────────────────
    agent_api_port: int = Field(default=8100, ge=1, le=65535)
    log_level: str = "INFO"

    # ── runtime.v1 seam — how we spawn the worker + register cron jobs ───────
    # The agent worker is spawned via runtime.v1 under this opaque profile (P11); routine jobs are
    # registered on the same runtime's schedule.v1 surface.
    runtime_api_url: str = "http://runtime-api:8090"
    agent_profile: str = "agent"
    # How the runtime's scheduler reaches THIS service's /invocations sink when a routine fires.
    agent_api_self_url: str = "http://agent-api:8100"

    # ── workspace.v1 seam — the bucket-backed git folders the dispatch mounts ─
    # The dispatch carries a LIST of workspace ids+modes; the Runtime materializes them from the
    # workspace store (bucket) into the container at `workspace_path` (mode is the write-access truth).
    workspace_path: str = "/workspace"
    workspace_ref: str = "main"
    # The workspace store (object bucket) the Runtime syncs granted workspaces down from / rw back to.
    workspace_store_url: str = "s3://vexa-workspaces"
    # The Runtime binds THIS (a host path or a docker named volume) at `workspaces_dir` in the worker —
    # the dev backing for the Workspace store (prod = a bucket-materialized path). The worker works in
    # the subject's subdir of it.
    workspace_mount_source: str = "agent-workspaces"

    # ── identity seam — the subject is the authenticated user (P20) ──────────
    # agent-api is fronted by the gateway, which resolves the api-key → user_id and injects X-User-Id.
    # The subject (workspace/quota/chat partition) is derived SERVER-SIDE from that header, never from the
    # client body. ``agent_default_subject`` is the single-user fallback for a direct/self-host deploy with
    # no gateway in front: empty (default) = FAIL-CLOSED (401 when X-User-Id is absent). Compose sets it to
    # keep the shared-user dev stack working until the terminal routes through the gateway (Stage 4).
    agent_default_subject: str = ""

    # ── Stream primitive — the per-dispatch redis Streams (unit:<id>:out / :in) ─
    redis_url: str = "redis://redis:6379/0"

    # ── MVP0 chat runner — claude turn over a per-subject local git workspace ─
    # The chat unit's per-person workspace dirs live here; seeded from the template (CLAUDE.md +
    # conventions). The claude model alias/name (subscription default if empty).
    workspaces_dir: str = "/workspaces"
    # Registry of workspace templates (workspace-seeds/<name>/); `default_template` selects one.
    # `seeding.resolve_seed_dir` is the selection seam (honors the VEXA_WORKSPACE_SEED_DIR override).
    workspace_seeds_dir: str = "/app/workspace-seeds"
    default_template: str = "default"  # light ready-to-go scaffold (README = onboarding-dashboard); override with VEXA_DEFAULT_TEMPLATE=finos for the FINOS KG seed
    # ── three-tier mount stack (AMENDMENT 4) — the GLOBAL SYSTEM tier (_global) ──
    # The platform-owned, READ-ONLY _global workspace mounted into EVERY worker (behaviour/skills/tools).
    # A host path / repo dir. Dispatch fails closed while empty or invalid: _global is mandatory.
    # A live MOUNT (updating this ONE repo propagates to all agents next turn), not a copy-once seed.
    global_system_workspace_path: str = ""
    # Pin the _global mount to a ref (branch/tag/sha) for safe rollout; empty = mount HEAD (main).
    global_system_workspace_ref: str = ""
    # Comma-separated user ids whose workers mount _global READ-WRITE — the admin setup
    # conversation writes the org tier; everyone else stays ro. Empty = nobody writes.
    global_admin_subjects: str = ""
    agent_model: str = ""
    meeting_model: str = ""
    # ── llm module dials (provider-agnostic; see core/agent/llm/README.md) ────
    # Non-secret operator config forwarded into workers by dispatch. The SECRETS
    # (VEXA_LLM_API_KEY / VEXA_LLM_BASE_URL) deliberately have no Settings field — they travel by
    # runtime credential brokering (docker_backend), same as ANTHROPIC_*.
    llm_provider: str = ""      # CompletionPort adapter key (openai-compat | anthropic); empty = default
    llm_model: str = ""         # deployment-default model (free string)
    model_allowlist: str = ""   # optional comma-separated gate on workspace-pinned models
    meeting_idle_timeout_sec: int = Field(default=4 * 60 * 60, ge=60)
    # Development-only, schema-validated JSON for post-meeting delivery to an SMTP sink such as
    # Mailpit. Empty keeps the adapter disabled. Production delivery uses a separate EmailSink.
    post_meeting_dev_email: str = ""
    # How long a CHAT worker serves its unit:<id>:in topic after the last turn before exiting
    # (TTL-on-idle). A live worker takes the thread's next message WARM (no container/CLI cold
    # start) — the window is the warm-hit budget; an idle worker costs only its parked memory.
    chat_idle_timeout_sec: int = Field(default=900, ge=30)

    # ── MVP3 toolbelt — tool.v1 descriptors + MCP launch specs (the generic tool mechanism) ──
    # A unit's unit.v1.tools names resolve against this dir into --allowedTools + an .mcp.json.
    tools_seed_dir: str = "/app/tools-seed"

    # Workspace-authored routines are reconciled from /workspaces/*/routines/*.md onto the durable
    # runtime scheduler. Set to 0 to disable the background reconciler.
    routine_reconcile_interval_sec: int = Field(default=60, ge=0)

    # ── membership index seam (Lane M) — users.data.memberships[] lives in Postgres, reachable ONLY
    # from the identity admin-api; agent-api holds the AUTHORITATIVE store (policy/members.json in the
    # workspace git repo) and mirrors the derived index over this internal edge. Empty base URL = the
    # in-memory index (git files stay authoritative; only the "shared with me" listing is degraded).
    admin_api_url: str = ""                       # e.g. http://admin-api:8001; empty = no index mirror
    # meeting-api base URL — agent-api hits GET /meetings/{id} on it to OWNER-SCOPE the live SSE stream
    # (P0 cross-tenant leak fix, SSE sibling): the caller-supplied meeting_id (row id) is verified to
    # belong to the authenticated X-User-Id BEFORE the redis transcript stream is opened, mirroring the
    # WS /ws authorize_subscribe ownership gate. meeting-api trusts the gateway-injected X-User-Id the
    # same way its own /transcripts/by-id path does.
    meeting_api_url: str = "http://meeting-api:8080"
    # The X-Internal-Secret the admin-api's internal tier checks (same value the gateway uses). SecretStr.

    # ── the scaffold seam (PRD 5.5) — where a person ARRIVES ────────────────
    # The terminal a scaffold link points at. ONE deployment fact, ONE variable, and deliberately
    # the SAME name flows already reads (`VEXA_UI_URL`, flows_steps/common.py): a link that names a
    # host the person cannot reach is worse than no link, and two spellings of the host is how that
    # happens. Empty means `POST /internal/scaffolds` refuses to mint rather than returning a url
    # with no origin - the mint is what a step checks before it sends, so it fails LOUDLY here.
    ui_url: str = ""

    # ── vexa-control MCP seam — the authenticated toolbelt a chat worker gets ─
    # The MCP endpoint a spawned worker connects to, carrying a short-lived delegation token minted
    # per dispatch (see shared.delegation). Empty ⇒ no MCP is attached (the pre-delegation behaviour).
    mcp_url: str = ""
    # How long that delegation token lives. It only has to outlast ONE turn — the chat worker's warm
    # window is the real bound — so it is deliberately short: a leaked worker env goes stale on its own.
    mcp_delegation_ttl_sec: int = 3600

    # ── secrets (never logged, committed, or in goldens) — P14 / P15 ─────────
    # Brokered, scoped identity the worker presents (ADR-0003): a port, not a raw key here.
    agent_identity_token: SecretStr = SecretStr("")
    # The shared key the Identity service signs per-dispatch tokens with (dev tier); every boundary
    # verifies with the same key. k8s replaces this with SPIRE-issued SVIDs behind the same interface.
    dispatch_signing_key: SecretStr = SecretStr("dev-dispatch-signing-key")
    # Internal-tier shared secret for the admin-api membership-index edge (Lane M).
    internal_api_secret: SecretStr = SecretStr("")
    # admin-api's ADMIN token (``X-Admin-API-Key`` / its ``ADMIN_API_TOKEN``). Needed ONLY to resolve
    # a meeting participant's ADDRESS to a subject for the post-meeting room, because the one route
    # that answers that question — ``GET /admin/users/email/{email}`` — sits behind the admin token
    # and NOT behind the internal-secret tier agent-api already holds.
    #
    # This is a BIG credential for a small question: the same token can create users, patch users and
    # read the whole directory. It is therefore EMPTY BY DEFAULT and the feature degrades to an empty
    # room without it (never to a guess). The narrow fix is an internal-tier
    # ``GET /internal/users/by-email/{email}`` on admin-api returning only ``{id}``, at which point
    # this field can be deleted — see ``control_plane.meeting_room`` and the room resolver in
    # ``control_plane.api``. Until that route exists, an operator who wants the room opts in here.
    admin_api_token: SecretStr = SecretStr("")
    # The symmetric key agent-api signs the worker's MCP DELEGATION token with, and the Vexa control
    # MCP verifies with (shared.delegation). Empty ⇒ the feature is OFF and no worker is given an MCP
    # credential at all: a delegation token signed with a zero-length key would verify for anyone who
    # guessed the format, so "unset" must mean ABSENT, never "signed with nothing".
    mcp_delegation_secret: SecretStr = SecretStr("")
    # The ONE server-side key every stored credential is sealed with (control_plane.secret_store): the
    # user's saved GitHub PAT and every workspace DEPLOY KEY's private half. Empty is NOT "no
    # encryption" — the store then generates a 0600 key under the secrets root on first use, so a
    # self-hoster gets encryption without configuring anything. Set this when the key must live OUTSIDE
    # the data volume (rotating it makes every previously-sealed secret unreadable, which reads as "no
    # credential saved" — deliberately, so a wrong key never decrypts to garbage).
    secrets_key: SecretStr = SecretStr("")

    def is_secret_present(self) -> bool:
        """True when a scoped identity token has been provided (without revealing it)."""
        return bool(self.agent_identity_token.get_secret_value())


def load_settings(**overrides: object) -> Settings:
    """Boot the config, validating against the model. Raises ``ValidationError`` → fail fast."""
    return Settings(**overrides)  # type: ignore[arg-type]
