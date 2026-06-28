"""Profile → Runnable registry. A `profile` is opaque in runtime.v1 (P11); the kernel resolves it to
HOW to run it — an `image` (container backends) and/or a `command` (process backend / container
override). The contract never sees this; it's kernel config (policy), per deployment.

This is the REAL registry (it replaces the old `test-sleep` stub). It resolves the two workload kinds
the control plane spawns, derived from 0.11's `profiles.yaml`:

  • meeting-bot — image `${BROWSER_IMAGE}`, the bot's constructor delivered as one env var
                 `VEXA_BOT_CONFIG` (invocation.v1 / ADR-0002).
  • agent      — the Claude Code agent; env mirrors runtime.v1 golden `spec-agent.json`
                 (scoped identity token + workspace repo/ref/path).

A Profile bundles the opaque Runnable with deployment defaults (idle/lifetime timeouts and a base env
the spec's env is layered on top of). Tests inject their own ProfileRegistry, so the eval never needs
a real image."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Runnable:
    image: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass(frozen=True)
class Profile:
    """An opaque workload kind: how to run it (Runnable) plus deployment defaults."""

    name: str
    runnable: Runnable
    idle_timeout_sec: Optional[int] = None
    max_lifetime_sec: Optional[int] = None
    # Base env the profile always sets; the spec's env is layered on top at create() time.
    base_env: dict[str, str] = field(default_factory=dict)


class ProfileRegistry:
    """Resolves a profile name → Runnable (what the kernel needs) and exposes the full Profile
    (for enforcement defaults). Unknown names resolve to None so the kernel returns the 400 the
    contract expects."""

    def __init__(self, runnables_or_profiles) -> None:
        self._profiles: dict[str, Profile] = {}
        for name, value in runnables_or_profiles.items():
            if isinstance(value, Profile):
                self._profiles[name] = value
            elif isinstance(value, Runnable):
                self._profiles[name] = Profile(name=name, runnable=value)
            else:
                raise TypeError(f"profile {name!r}: expected Profile or Runnable, got {type(value)}")

    def resolve(self, name: str) -> Optional[Runnable]:
        profile = self._profiles.get(name)
        return profile.runnable if profile else None

    def get(self, name: str) -> Optional[Profile]:
        return self._profiles.get(name)

    def names(self) -> list[str]:
        return list(self._profiles)


def worker_image_for(agent_image: str) -> str:
    """The image a SPAWNED agent worker runs under. It is byte-identical to the agent-api image
    (`AGENT_IMAGE`) — workers ARE that image — but carried under a DISTINCT name so `docker images`
    /`docker ps` no longer show every ephemeral worker as the agent-api service. Env-configurable via
    `AGENT_WORKER_IMAGE`; defaults to the agent-api image's repo with `-api` swapped for `-worker`
    (preserving the `:${IMAGE_TAG}` tag), e.g. `vexaai/v012-agent-api:dev` → `vexaai/v012-agent-worker:dev`.
    Falls back to the agent-api image itself when no derivation is possible (empty/odd name)."""
    override = os.environ.get("AGENT_WORKER_IMAGE", "").strip()
    if override:
        return override
    if not agent_image:
        return agent_image
    repo, sep, tag = agent_image.partition(":")  # split off the tag, keep it
    if repo.endswith("-agent-api"):
        repo = repo[: -len("-agent-api")] + "-agent-worker"
    elif repo.endswith("agent-api"):
        repo = repo[: -len("agent-api")] + "agent-worker"
    else:
        return agent_image  # can't derive a distinct name → keep agent-api (fail-safe)
    return f"{repo}{sep}{tag}"


def default_registry() -> ProfileRegistry:
    """The real, deployment-shaped registry. Images come from env (no `:latest` fallback — a missing
    image surfaces as an empty string the backend rejects, matching 0.11's fail-visible stance)."""
    browser_image = os.environ.get("BROWSER_IMAGE", "")
    agent_image = os.environ.get("AGENT_IMAGE", "")
    # Workers run the agent-api BYTES under a distinct NAME (see worker_image_for). The runtime ensures
    # this name exists as a local tag alias of AGENT_IMAGE at startup (build_production_app); dispatch
    # falls back to AGENT_IMAGE if the alias is missing, so spawn never breaks.
    agent_worker_image = worker_image_for(agent_image)
    return ProfileRegistry(
        {
            # Meeting bot — Playwright browser; lifetime managed by meeting-api, so no idle timeout.
            # The bot's whole config arrives as one env var VEXA_BOT_CONFIG (invocation.v1).
            "meeting-bot": Profile(
                name="meeting-bot",
                runnable=Runnable(
                    image=browser_image,
                    command=["/app/vexa-bot/entrypoint.sh"],
                ),
                idle_timeout_sec=0,  # 0 ⇒ managed externally; enforcement skips it
                base_env={},
            ),
            # Claude Code agent — the in-container worker harness (worker): consumes the
            # dispatch from env, runs the governed turn over the mounted workspace, XADDs UnitEvents to
            # unit:<id>:out, serves unit:<id>:in until idle. Continuity is the session file in the
            # workspace, so a reaped+respawned container resumes instantly.
            "agent": Profile(
                name="agent",
                runnable=Runnable(
                    image=agent_worker_image,
                    command=["python", "-m", "worker"],
                ),
                idle_timeout_sec=300,
                max_lifetime_sec=3600,
                base_env={},
            ),
        }
    )
