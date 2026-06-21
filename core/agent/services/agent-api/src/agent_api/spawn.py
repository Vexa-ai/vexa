"""Build the ``runtime.v1`` WorkloadSpec ``env`` for an agent worker.

The agent worker is spawned via runtime.v1 (profile ``agent``) with the workspace repo + a scoped
identity token in its ``env`` — exactly the shape of golden ``runtime.v1/spec-agent.json`` (P8):

    VEXA_AGENT_IDENTITY_TOKEN · VEXA_WORKSPACE_REPO · VEXA_WORKSPACE_REF · VEXA_WORKSPACE_PATH

This is the only place the agent-api shapes the runtime seam; the token is read from validated
config (a SecretStr) and never logged.
"""
from __future__ import annotations

from .config import Settings


def build_worker_env(settings: Settings, workspace_repo: str) -> dict[str, str]:
    """Map config + a target workspace to the worker's runtime.v1 env (12-factor, P7)."""
    return {
        "VEXA_AGENT_IDENTITY_TOKEN": settings.agent_identity_token.get_secret_value(),
        "VEXA_WORKSPACE_REPO": workspace_repo,
        "VEXA_WORKSPACE_REF": settings.workspace_ref,
        "VEXA_WORKSPACE_PATH": settings.workspace_path,
    }
