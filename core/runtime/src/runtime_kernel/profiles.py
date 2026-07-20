"""Profile → Runnable registry. A `profile` is opaque in runtime.v1 (P11); the kernel resolves it to
HOW to run it — an `image` (container backends) and/or a `command` (process backend / container
override). The contract never sees this; it's kernel config (policy), per deployment.

This is the REAL registry (it replaces the old `test-sleep` stub). It resolves the two workload kinds
the control plane spawns, derived from 0.11's `profiles.yaml`:

  • meeting-bot — image `${BROWSER_IMAGE}` or the packaged ZAKI bot Pod contract, with the bot's
                 constructor delivered as one env var `VEXA_BOT_CONFIG` (invocation.v1 / ADR-0002).
  • agent      — the Claude Code agent; env mirrors runtime.v1 golden `spec-agent.json`
                 (scoped identity token + workspace repo/ref/path).

A Profile bundles the opaque Runnable with deployment defaults (idle/lifetime timeouts and a base env
the spec's env is layered on top of). Tests inject their own ProfileRegistry, so the eval never needs
a real image."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field, replace
from typing import Optional

from .bot_contract import BotPodContract


@dataclass(frozen=True)
class Runnable:
    image: Optional[str] = None
    command: Optional[list[str]] = None
    bot_pod_contract: Optional[BotPodContract] = None


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
    """The image a SPAWNED agent worker runs under — the DEDICATED worker build
    (core/agent/worker/Dockerfile: claude-code + node + the `worker` package), NOT the agent-api
    control-plane image (which ships no `worker` module and cannot serve a dispatch). Env-configurable
    via `AGENT_WORKER_IMAGE`; defaults to the agent-api image's repo with `-api` swapped for `-worker`
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
    bot_pod_contract = BotPodContract.from_env()
    browser_image = bot_pod_contract.image if bot_pod_contract else os.environ.get("BROWSER_IMAGE", "")
    agent_image = os.environ.get("AGENT_IMAGE", "")
    # Workers run their OWN image (see worker_image_for — core/agent/worker/Dockerfile, not the
    # agent-api image). The Docker backend ensures it is present at startup, pulling it when absent
    # (build_production_app → DockerBackend.ensure_worker_image).
    agent_worker_image = worker_image_for(agent_image)
    # The legacy/vendored browser image exposes the historical compatibility launcher, while the
    # ZAKI-owned image declares the real `/app/entrypoint.sh` as its image entrypoint.  Kubernetes
    # `command` replaces an image ENTRYPOINT, so the contracted image must receive its own stable
    # path rather than the legacy one.
    meeting_bot_command = (
        ["/app/entrypoint.sh"]
        if bot_pod_contract is not None
        else ["/app/vexa-bot/entrypoint.sh"]
    )
    speaker_stream_env = {
        key: os.environ[key]
        for key in (
            "BOT_SPEAKER_MIN_AUDIO_SEC",
            "BOT_SPEAKER_SUBMIT_INTERVAL_SEC",
            "BOT_SPEAKER_CONFIRM_THRESHOLD",
            "BOT_SPEAKER_MAX_BUFFER_SEC",
            "BOT_SPEAKER_IDLE_TIMEOUT_SEC",
        )
        if os.environ.get(key, "").strip()
    }
    return ProfileRegistry(
        {
            # Meeting bot — Playwright browser; lifetime managed by meeting-api, so no idle timeout.
            # The bot's whole config arrives as one env var VEXA_BOT_CONFIG (invocation.v1).
            # No command: the shipped bot image already declares the launcher as its ENTRYPOINT
            # (core/meetings/services/bot/Dockerfile: ENTRYPOINT ["/app/entrypoint.sh"]), so every
            # backend must exec THAT and nothing else. A profile command here would either be a
            # harmless trailing arg (docker Cmd-append) or REPLACE the entrypoint with a path that
            # does not exist in the image (k8s `kubectl run --command` → StartError). Leaving it
            # empty makes docker (omit Cmd) and k8s (omit --command) both boot the image entrypoint
            # identically. The process backend (lite) never uses this default — it sets BOT_COMMAND
            # to its in-container launcher (deploy/lite/bin/vexa-bot-launch), applied below.
            "meeting-bot": Profile(
                name="meeting-bot",
                runnable=Runnable(
                    image=browser_image,
                    command=meeting_bot_command,
                    bot_pod_contract=bot_pod_contract,
                ),
                idle_timeout_sec=0,  # 0 ⇒ managed externally; enforcement skips it
                base_env=speaker_stream_env,
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


# Per-deployment command overrides: env var → the profile whose Runnable.command it replaces. Under
# the container backends the meeting-bot command is empty (the image ENTRYPOINT is authoritative) and
# the agent command is the image's launch argv; a process-backend deployment (single-host `lite`) has
# no image ENTRYPOINT to lean on, so it MUST point these at in-container launchers that wire the right
# venv/PYTHONPATH/cwd before exec'ing the same workload (the process backend requires a command).
_COMMAND_OVERRIDE_ENV = {
    "meeting-bot": "BOT_COMMAND",
    "agent": "AGENT_WORKER_COMMAND",
}


def apply_command_overrides(registry: ProfileRegistry) -> ProfileRegistry:
    """Return a registry whose profile commands honor the env overrides in ``_COMMAND_OVERRIDE_ENV``.
    Additive + opt-in: a profile is rebuilt ONLY when its env var is set to a non-empty value (parsed
    shlex-style, so `/usr/local/bin/foo --flag` → the argv list Popen needs); with no overrides set
    (docker/k8s default) the registry is returned with every profile's original command intact."""
    rebuilt: dict[str, Profile] = {}
    for name in registry.names():
        profile = registry.get(name)
        env_name = _COMMAND_OVERRIDE_ENV.get(name)
        raw = os.environ.get(env_name, "").strip() if env_name else ""
        if raw:
            profile = replace(profile, runnable=replace(profile.runnable, command=shlex.split(raw)))
        rebuilt[name] = profile
    return ProfileRegistry(rebuilt)
