"""workload_env.py — the split between what a caller may say about a workload and what only the
runtime decides.

A spec's ``env`` is the caller's: a bot's constructor, a dispatch's identity and topics, and the
ORDERED MOUNT SET (``VEXA_MOUNTS``) naming which workspaces the workload sees. Where those workspaces
physically come from (the store's host path, named volume or PVC, and where it is bound) and where a
Pod is scheduled are properties of the runtime and its substrate, so they come from the runtime's
own configuration:

* keys under :data:`RUNTIME_OWNED_PREFIXES` are dropped from the spec — ``RUNTIME_K8S_*`` (Pod
  scheduling and Secret mounts) and ``VEXA_WORKSPACE_MOUNT_*`` (the store backing);
* the store backing is injected from the runtime's process env (:class:`StoreConfig`) whenever the
  spec declares a mount set;
* every mount in the set must sit strictly under the store root, and a mount carrying its own host
  ``source`` must name one of the sources the runtime was configured to allow (the ``_global``
  organisation tier, when it lives outside the store). Anything else refuses the spec with
  :class:`MountRefused` — a 400 at the API, before any substrate call.

Pure and env-driven, so it is exercised offline with plain dicts.
"""
from __future__ import annotations

import json
import os
import posixpath
from dataclasses import dataclass
from typing import Mapping, Optional

#: Spec env keys only the runtime may set. A caller-supplied value is discarded.
RUNTIME_OWNED_PREFIXES = ("RUNTIME_K8S_", "VEXA_WORKSPACE_MOUNT_")

STORE_SOURCE_ENV = "VEXA_WORKSPACE_MOUNT_SOURCE"
STORE_TARGET_ENV = "VEXA_WORKSPACE_MOUNT_TARGET"
#: The one out-of-store mount source a deployment may configure (agent-api's ``_global`` tier).
GLOBAL_SOURCE_ENV = "VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH"
DEFAULT_STORE_TARGET = "/workspaces"

MOUNTS_ENV = "VEXA_MOUNTS"
WORKSPACE_PATH_ENV = "VEXA_WORKSPACE_PATH"

#: Deployment dials and model credentials the runtime forwards from its OWN environment into an
#: agent WORKER (the runtime brokers model credentials). A value the dispatch already stamped wins.
#: Meeting bots receive none of these: they read no model credential.
WORKER_FORWARD_ENV = (
    # The harness runner selection and its gate. What remains of the completion dials is read by the
    # openai-agent HARNESS inside the worker (decision 37), so it is forwarded.
    # THE WORKER'S MODEL (decision 36): `engine.py` reads VEXA_AGENT_MODEL for every turn — the chat
    # turn AND the write-back phase — so a deployment that wants Sonnet sets one value here.
    "VEXA_AGENT_MODEL",
    "VEXA_LLM_BASE_URL",
    "VEXA_LLM_API_KEY",
    "VEXA_LLM_MODEL",
    # Server-specific request fields the OpenAI dialect cannot express. LOAD-BEARING for a
    # self-hosted Qwen ({"chat_template_kwargs":{"enable_thinking":false}}): without it the model
    # reasons its whole budget away and returns nothing parseable.
    "VEXA_LLM_EXTRA_BODY",
    "VEXA_MODEL_ALLOWLIST",
    "VEXA_RUNNER",
    "VEXA_MIDTURN_INJECT",
    "VEXA_CODEX_MODEL",
    # openai-agent harness budget dials (per-turn ceiling + context trim + streaming)
    "VEXA_AGENT_MAX_TOOL_CALLS",
    "VEXA_AGENT_MAX_TURN_SEC",
    "VEXA_AGENT_CONTEXT_TOKENS",
    "VEXA_AGENT_STREAM",
    # The worker's reach onto the open web (WebSearch/WebFetch). THE ENDPOINT IS THE OPERATOR'S —
    # nothing search-shaped ships with this product — so it arrives as deployment env and is
    # forwarded like every other worker-read dial.
    "VEXA_SEARCH_URL",
    "VEXA_SEARCH_DIALECT",
    "VEXA_SEARCH_API_KEY",
    # codex harness API-key auth (subscription auth is a read-only file mount)
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    # claude-code harness credentials (that adapter's concern only)
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)

#: What a CHILD PROCESS (the process backend) inherits from the runtime's environment besides the
#: worker forward list: what any program needs to run on this host (paths, locale, display and
#: audio, the browser install, proxies and CA bundles). Never product configuration and never a
#: service credential — those reach a child only through its own spec.
PROCESS_PLUMBING_ENV = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TZ", "TMPDIR",
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
    "DISPLAY", "XDG_RUNTIME_DIR", "PULSE_SERVER", "PULSE_SINK", "PULSE_SOURCE", "PULSE_RUNTIME_PATH",
    "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "PNPM_HOME", "NODE_ENV", "CI",
    "VEXA_HF_CACHE", "VEXA_IMAGE_VERSION",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
)


def forwarded_worker_env(parent: Mapping[str, str], env: Mapping[str, str]) -> dict[str, str]:
    """The :data:`WORKER_FORWARD_ENV` values ``parent`` (the runtime's environment) holds that the
    workload ``env`` does not already set."""
    return {k: parent[k] for k in WORKER_FORWARD_ENV if parent.get(k) and k not in env}


def child_environment(env: Mapping[str, str], *, worker: bool,
                      parent: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """A child process's COMPLETE environment, built from scratch: host plumbing from ``parent``,
    the worker forward list (agent workers only), then the workload's own ``env``. Nothing else of
    the runtime's environment — its caller token, any service secret it was started with — reaches
    the child."""
    parent = os.environ if parent is None else parent
    out = {k: parent[k] for k in PROCESS_PLUMBING_ENV if k in parent}
    if worker:
        out.update(forwarded_worker_env(parent, env))
    out.update(env)
    return out


class MountRefused(ValueError):
    """The spec's mount set names something outside what the runtime serves."""


@dataclass(frozen=True)
class StoreConfig:
    """The runtime's own view of the workspace store. ``source`` is what backs it on the substrate
    (a host path or named volume for docker, the PVC claim name for k8s, nothing for the process
    backend, which shares the filesystem); ``target`` is where it sits inside a workload;
    ``extra_sources`` are the out-of-store host paths a mount may name as its ``source``."""

    source: str = ""
    target: str = DEFAULT_STORE_TARGET
    extra_sources: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "StoreConfig":
        env = os.environ if env is None else env
        target = (env.get(STORE_TARGET_ENV) or "").strip() or DEFAULT_STORE_TARGET
        global_src = (env.get(GLOBAL_SOURCE_ENV) or "").strip()
        return cls(
            source=(env.get(STORE_SOURCE_ENV) or "").strip(),
            target=_clean_abs(target, what=STORE_TARGET_ENV),
            extra_sources=(_clean_abs(global_src, what=GLOBAL_SOURCE_ENV),) if global_src else (),
        )


def _clean_abs(path: str, *, what: str) -> str:
    """``path`` as a normalized absolute POSIX path, refusing relative paths and ``..`` segments."""
    if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
        raise MountRefused(f"{what} must be an absolute path without '..' segments")
    return posixpath.normpath(path)


def _strictly_under(path: str, root: str) -> bool:
    return path != root and path.startswith(root.rstrip("/") + "/")


def declared_mounts(env: Mapping[str, str]) -> list[dict]:
    """The spec's mount set, parsed strictly: ``VEXA_MOUNTS`` must be a JSON array of objects that
    each carry a string ``path``. Absent ⇒ ``[]``."""
    raw = env.get(MOUNTS_ENV)
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MountRefused(f"{MOUNTS_ENV} is not valid JSON") from exc
    if not isinstance(data, list) or not all(
        isinstance(m, dict) and isinstance(m.get("path"), str) and m["path"] for m in data
    ):
        raise MountRefused(f"{MOUNTS_ENV} must be a JSON array of mount objects, each with a path")
    return data


def check_mounts(env: Mapping[str, str], store: StoreConfig) -> None:
    """Refuse any mount the runtime does not serve. Raises :class:`MountRefused`."""
    for m in declared_mounts(env):
        path = _clean_abs(m["path"], what=f"{MOUNTS_ENV} path")
        source = m.get("source")
        if source:
            if not isinstance(source, str) or _clean_abs(source, what=f"{MOUNTS_ENV} source") not in store.extra_sources:
                raise MountRefused(f"{MOUNTS_ENV}: mount {m.get('slug')!r} names a source this runtime does not serve")
            continue
        if not _strictly_under(path, store.target):
            raise MountRefused(f"{MOUNTS_ENV}: mount {m.get('slug')!r} is outside the workspace store")
    cwd = env.get(WORKSPACE_PATH_ENV)
    if cwd and not _strictly_under(_clean_abs(cwd, what=WORKSPACE_PATH_ENV), store.target):
        raise MountRefused(f"{WORKSPACE_PATH_ENV} is outside the workspace store")


def workload_env(spec_env: Mapping[str, str], store: StoreConfig) -> dict[str, str]:
    """The environment a backend receives for one workload: the caller's env without the
    runtime-owned keys, its mount set checked against ``store``, and — when it declares one — the
    runtime's own store backing. Raises :class:`MountRefused`."""
    env = {k: v for k, v in spec_env.items() if not k.startswith(RUNTIME_OWNED_PREFIXES)}
    check_mounts(env, store)
    if env.get(MOUNTS_ENV) or env.get(WORKSPACE_PATH_ENV):
        env[STORE_TARGET_ENV] = store.target
        if store.source:
            env[STORE_SOURCE_ENV] = store.source
    return env
