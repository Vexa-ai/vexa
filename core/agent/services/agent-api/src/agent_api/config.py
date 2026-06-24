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

    # ── runtime.v1 seam — how we spawn the worker ────────────────────────────
    # The agent worker is spawned via runtime.v1 under this opaque profile (P11).
    runtime_api_url: str = "http://runtime-api:8090"
    agent_profile: str = "agent"

    # ── workspace.v1 seam — the user git repo the worker mounts ──────────────
    # These become the worker's `env` per runtime.v1 (see golden spec-agent.json).
    workspace_path: str = "/workspace"
    workspace_ref: str = "main"

    # ── MVP0 chat runner — claude turn over a per-subject local git workspace ─
    # The chat unit's per-person workspace dirs live here; seeded from the template (CLAUDE.md +
    # conventions). The claude model alias/name (subscription default if empty).
    workspaces_dir: str = "/workspaces"
    workspace_seed_dir: str = "/app/workspace-seed"
    agent_model: str = ""

    # ── secrets (never logged, committed, or in goldens) — P14 / P15 ─────────
    # Brokered, scoped identity the worker presents (ADR-0003): a port, not a raw key here.
    agent_identity_token: SecretStr = SecretStr("")

    def is_secret_present(self) -> bool:
        """True when a scoped identity token has been provided (without revealing it)."""
        return bool(self.agent_identity_token.get_secret_value())


def load_settings(**overrides: object) -> Settings:
    """Boot the config, validating against the model. Raises ``ValidationError`` → fail fast."""
    return Settings(**overrides)  # type: ignore[arg-type]
