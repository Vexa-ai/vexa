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
import shlex
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional

from . import pod_scheduling
from .models import Resources
from .pod_scheduling import PodScheduling
from .workload_env import CODEX_HOME_ENV, WORKER_CODEX_HOME, WORKER_FORWARD_ENV


#: The label a workload's class rides on, on every substrate (container labels, Pod labels). The
#: chart's NetworkPolicies select on it. Its values are profile data (``default_registry``) — never
#: taken from the caller's spec or workload id.
CLASS_LABEL = "vexa.role"


@dataclass(frozen=True)
class SourceMount:
    """A development hot-mount: the host path in the runtime setting ``env`` is bound read-only at
    ``target`` (container backends), which becomes the working directory and heads ``pythonpath``."""

    env: str
    target: str
    pythonpath: str


@dataclass(frozen=True)
class CredentialFile:
    """A credential file the runtime hands to a workload whose profile asks for credentials.

    ``source`` is the file as the runtime's substrate sees it (a docker-host path the daemon binds;
    a path the process backend reads). ``target`` is where a container workload finds it (absolute).
    ``home_path`` is where a process-backend child finds it, relative to its private HOME; empty means
    the file is for containers only."""

    source: str
    target: str
    home_path: str = ""


@dataclass(frozen=True)
class Runnable:
    """How to run one kind of workload, and what the runtime gives it beyond its spec.

    Everything past ``image``/``command`` is profile data that every backend applies the same way, so
    no backend knows what kind of workload it is starting: which labels it carries, which network it
    joins, which of the runtime's own settings are forwarded into it, whether the runtime's
    credential files are mounted into it, and (on Kubernetes) where its Pods are placed."""

    image: Optional[str] = None
    command: Optional[list[str]] = None
    #: Labels the workload carries on its substrate.
    labels: Mapping[str, str] = field(default_factory=dict)
    #: The runtime setting naming the container network this workload joins. Unset, or empty in the
    #: runtime's environment ⇒ ``DOCKER_NETWORK``.
    network_env: Optional[str] = None
    #: Settings forwarded from the runtime's own environment into the workload, unless the spec
    #: already sets them.
    forward_env: tuple[str, ...] = ()
    #: The workload receives the runtime's configured credentials: ``credential_files`` and
    #: ``credential_env`` below, and on k8s the ``RUNTIME_K8S_SECRET_MOUNTS`` Secrets. A profile
    #: without it (a meeting bot) is given none.
    credential_mounts: bool = False
    #: A development hot-mount the docker backend applies when its runtime setting is set.
    source_mount: Optional[SourceMount] = None
    #: Where the k8s backend places this workload's Pods (node selector, tolerations, priority
    #: class, image pull secrets) — operator configuration read at boot, never from a spec.
    scheduling: PodScheduling = field(default_factory=PodScheduling)
    #: The credential files the runtime hands this workload (see :class:`CredentialFile`).
    credential_files: tuple[CredentialFile, ...] = ()
    #: Settings a container workload that receives credentials is given, unless its spec sets them
    #: (where its harness finds the files). A process-backend child has a private HOME instead.
    credential_env: Mapping[str, str] = field(default_factory=dict)
    #: The host groups a process-backend child joins besides its own (by name; one the host lacks is
    #: skipped). Container backends ignore it.
    process_groups: tuple[str, ...] = ()
    #: A process-backend child may create user namespaces (a meeting bot: Chromium's sandbox is built
    #: on one). Every other child of a root process backend loses that ability before it runs
    #: (runtime_kernel.userns). Container backends leave it to the container's seccomp profile.
    user_namespaces: bool = False
    #: The Linux capabilities a container workload keeps; every other one is dropped, and it can gain
    #: none (docker ``no-new-privileges``, k8s ``allowPrivilegeEscalation: false``, seccomp
    #: ``RuntimeDefault``). A process-backend child keeps none: it is not root.
    capabilities: tuple[str, ...] = ()
    #: The image runs as a non-root user, so k8s may require it (``runAsNonRoot``).
    run_as_non_root: bool = False


@dataclass(frozen=True)
class Profile:
    """An opaque workload kind: how to run it (Runnable) plus deployment defaults."""

    name: str
    runnable: Runnable
    idle_timeout_sec: Optional[int] = None
    max_lifetime_sec: Optional[int] = None
    # Base env the profile always sets; the spec's env is layered on top at create() time.
    base_env: dict[str, str] = field(default_factory=dict)
    # Deployment-wide sizing for THIS workload class, independent of every other class (the chart
    # renders it per profile). Applied at create() when the caller emits no resources of its own;
    # an explicit spec.resources always wins. None ⇒ unsized (the optional contract preserved).
    resources: Optional[Resources] = None


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


def network_envs(registry: "ProfileRegistry") -> tuple[str, ...]:
    """The runtime settings naming the networks this registry's workloads join (besides
    ``DOCKER_NETWORK``) — what the docker backend scopes discovery by."""
    keys = (registry.get(n).runnable.network_env for n in registry.names())
    return tuple(dict.fromkeys(k for k in keys if k))


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


# Per-profile spawned-workload sizing, read from the runtime's OWN env (the chart renders it from
# runtime.workloadResources.<class>). Distinct keys per class: a meeting bot is a Chromium browser,
# an agent worker is a code harness — they are sized independently, never off one shared knob.
_RESOURCE_ENV = {
    "meeting-bot": ("RUNTIME_BOT_CPU", "RUNTIME_BOT_MEMORY_MB"),
    "agent": ("RUNTIME_AGENT_WORKER_CPU", "RUNTIME_AGENT_WORKER_MEMORY_MB"),
}


def _numeric_env(key: str, cast):
    """One resource knob from env. Unset/empty ⇒ None (unsized). A non-numeric value is FATAL:
    a resource constraint silently dropped is exactly the defect this seam closes — an unsized Pod
    that a quota-controlled namespace rejects, or admits without the bound the operator asked for."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return None
    try:
        return cast(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be numeric, got {raw!r}") from exc


def _profile_resources(profile: str) -> Optional[Resources]:
    cpu_key, mem_key = _RESOURCE_ENV[profile]
    cpu = _numeric_env(cpu_key, float)
    memory_mb = _numeric_env(mem_key, int)
    if cpu is None and memory_mb is None:
        return None
    return Resources(cpu=cpu, memoryMb=memory_mb)


# Per-profile Pod placement on Kubernetes, read from the runtime's OWN env at boot (the chart renders
# it from runtime.workloadScheduling.<class>) and validated there: <prefix>NODE_SELECTOR,
# <prefix>TOLERATIONS, <prefix>PRIORITY_CLASS_NAME, <prefix>IMAGE_PULL_SECRETS. The prefix sits under
# RUNTIME_K8S_, which a spec's env can never set (workload_env.RUNTIME_OWNED_PREFIXES).
_SCHEDULING_ENV_PREFIX = {
    "meeting-bot": "RUNTIME_K8S_BOT_",
    "agent": "RUNTIME_K8S_AGENT_WORKER_",
}


def _profile_scheduling(profile: str) -> PodScheduling:
    return pod_scheduling.from_env(_SCHEDULING_ENV_PREFIX[profile], os.environ)
#: The claude CLI's credential file, relative to its config directory (``~/.claude``).
CLAUDE_CREDENTIALS_FILENAME = ".credentials.json"


def host_claude_credentials(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The path of the claude subscription credential the operator configured, as the runtime's
    substrate sees it (a docker-host path for docker, an in-container path for the process backend).

    ``HOST_CLAUDE_CREDENTIALS`` (the file) wins when set; otherwise it is derived from
    ``HOST_CLAUDE_DIR`` (the host's ``~/.claude``), which is the mount shape that survives a token
    refresh — the CLI replaces ``.credentials.json`` by ``rename(2)``, i.e. with a NEW INODE, and a
    single-FILE bind is pinned to the inode it was created with. ``None`` = no subscription file
    configured (an API-style key may still be brokered as env)."""
    env = os.environ if env is None else env
    explicit = (env.get("HOST_CLAUDE_CREDENTIALS") or "").strip()
    if explicit:
        return explicit
    host_dir = (env.get("HOST_CLAUDE_DIR") or "").strip()
    return f"{host_dir.rstrip('/')}/{CLAUDE_CREDENTIALS_FILENAME}" if host_dir else None


def configured_credentials(env: Optional[Mapping[str, str]] = None) -> tuple[CredentialFile, ...]:
    """The model-subscription files the operator configured for the runtime to hand to workers —
    the one place that knows which harness reads which file: the claude CLI's credential
    (``HOST_CLAUDE_CREDENTIALS`` / ``HOST_CLAUDE_DIR``) and the Codex ``auth.json``
    (``HOST_CODEX_CREDENTIALS``, inside ``WORKER_CODEX_HOME`` in a container, ``~/.codex`` for a
    process-backend child)."""
    env = os.environ if env is None else env
    files: list[CredentialFile] = []
    claude = host_claude_credentials(env)
    if claude:
        files.append(CredentialFile(source=claude, target=f"/root/.claude/{CLAUDE_CREDENTIALS_FILENAME}",
                                    home_path=f".claude/{CLAUDE_CREDENTIALS_FILENAME}"))
    codex = (env.get("HOST_CODEX_CREDENTIALS") or "").strip()
    if codex:
        files.append(CredentialFile(source=codex, target=f"{WORKER_CODEX_HOME}/auth.json",
                                    home_path=".codex/auth.json"))
    return tuple(files)


#: What an agent worker image running as root keeps: it hands the model's tools a user of their own
#: (SETUID, SETGID), grants that user the turn's workspaces by group and setgid directories (CHOWN,
#: FOWNER, FSETID), reads and commits what the tools wrote (DAC_OVERRIDE), and stops the tools'
#: process (KILL). A meeting bot needs none.
WORKER_CAPABILITIES = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "SETGID", "SETUID")


def default_registry() -> ProfileRegistry:
    """The real, deployment-shaped registry. Images come from env (no `:latest` fallback — a missing
    image surfaces as an empty string the backend rejects, matching 0.11's fail-visible stance)."""
    browser_image = os.environ.get("BROWSER_IMAGE", "")
    agent_image = os.environ.get("AGENT_IMAGE", "")
    # Workers run their OWN image (see worker_image_for — core/agent/worker/Dockerfile, not the
    # agent-api image). The Docker backend ensures it is present at startup, pulling it when absent
    # (build_production_app → DockerBackend.ensure_worker_image).
    agent_worker_image = worker_image_for(agent_image)
    bot_tuning_env = {
        key: os.environ[key]
        for key in (
            "BOT_ALONE_SILENCE_WINDOW_MS",
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
                    command=None,
                    # A meeting bot joins DOCKER_NETWORK (meeting-api for its callbacks and uploads,
                    # redis for its streams) and is given no model credential.
                    labels={CLASS_LABEL: "bot"},
                    # As a process-backend child it joins no host group: it brings up its own
                    # display and audio server (deploy/lite/bin/vexa-bot-launch). It may create user
                    # namespaces, which its browser's sandbox needs; no other child may.
                    scheduling=_profile_scheduling("meeting-bot"),
                    user_namespaces=True,
                    # The bot image runs as a non-root uid (Chromium will not sandbox a root browser).
                    run_as_non_root=True,
                ),
                idle_timeout_sec=0,  # 0 ⇒ managed externally; enforcement skips it
                base_env=bot_tuning_env,
                resources=_profile_resources("meeting-bot"),
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
                    # An agent worker joins its own network (gateway, redis, flows-api — no internal
                    # service), and the runtime brokers model credentials and dials into it.
                    labels={CLASS_LABEL: "worker"},
                    network_env="DOCKER_WORKER_NETWORK",
                    forward_env=WORKER_FORWARD_ENV,
                    credential_mounts=True,
                    credential_files=configured_credentials(),
                    # The Codex home is the runtime's to name: a credential is mounted at
                    # <it>/auth.json, and the worker and the Codex CLI read CODEX_HOME.
                    credential_env={CODEX_HOME_ENV: WORKER_CODEX_HOME},
                    capabilities=WORKER_CAPABILITIES,
                    source_mount=SourceMount(env="VEXA_AGENT_SRC_MOUNT", target="/app/src/agent_api",
                                             pythonpath="/app/src/agent_api:/app"),
                    scheduling=_profile_scheduling("agent"),
                ),
                idle_timeout_sec=300,
                max_lifetime_sec=3600,
                base_env={},
                resources=_profile_resources("agent"),
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
