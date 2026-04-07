"""YAML-based profile loader with hot-reload via SIGHUP.

Profiles are declarative container templates. Callers reference them by name
when creating containers. The runtime resolves the profile to an image, resource
limits, idle timeout, and other defaults.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from runtime_api import config

logger = logging.getLogger("runtime_api.profiles")

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

_profiles: dict[str, dict] = {}
_lock = threading.Lock()
_mtime: float = 0.0


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in strings."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            expr = m.group(1)
            if ":-" in expr:
                var, default = expr.split(":-", 1)
                return os.environ.get(var, default)
            return os.environ.get(expr, m.group(0))
        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value

# Default profile schema with all supported fields
PROFILE_DEFAULTS = {
    "image": "",
    "command": None,
    "resources": {
        "cpu_request": None,
        "cpu_limit": None,
        "memory_request": None,
        "memory_limit": None,
        "shm_size": 0,
    },
    "idle_timeout": 300,
    "auto_remove": True,
    "ports": {},
    "mounts": [],
    "env": {},
    "gpu": False,
    "gpu_type": None,
    "node_selector": {},
    "working_dir": None,
    "k8s_overrides": {},  # opaque K8s-specific: tolerations, affinity, annotations
}


# Builtin profiles — minimal fallbacks for the two profiles actually in use.
# profiles.yaml overrides these. No :latest tags, no localhost fallbacks.
BUILTIN_PROFILES = {
    "meeting": {
        "image": "${BROWSER_IMAGE}",
        "idle_timeout": 0,
        "auto_remove": False,
    },
    "agent": {
        "image": "${AGENT_IMAGE}",
        "command": ["sleep", "infinity"],
        "idle_timeout": 300,
        "auto_remove": False,
    },
}


def load_profiles(path: str | None = None) -> dict[str, dict]:
    """Load profiles from YAML file, merged on top of built-in defaults. Thread-safe."""
    global _profiles, _mtime
    path = path or config.PROFILES_PATH

    if not Path(path).exists():
        logger.info(f"No profiles file at {path} — using built-in defaults")
        with _lock:
            _profiles = {
                name: {**PROFILE_DEFAULTS, **_expand_env_vars(spec),
                       "resources": {**PROFILE_DEFAULTS["resources"], **_expand_env_vars(spec).get("resources", {})}}
                for name, spec in BUILTIN_PROFILES.items()
            }
        return _profiles

    try:
        current_mtime = Path(path).stat().st_mtime
        with _lock:
            if current_mtime == _mtime and _profiles:
                return _profiles

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        # Start with builtins, then overlay YAML definitions
        profiles = {}
        all_specs = {**BUILTIN_PROFILES}
        for name, spec in raw.get("profiles", raw).items():
            all_specs[name] = spec  # YAML overrides builtins

        for name, spec in all_specs.items():
            spec = _expand_env_vars(spec)
            merged = {**PROFILE_DEFAULTS}
            merged.update(spec)
            # Merge resources sub-dict
            if "resources" in spec:
                merged["resources"] = {**PROFILE_DEFAULTS["resources"], **spec["resources"]}
            profiles[name] = merged

        with _lock:
            _profiles = profiles
            _mtime = current_mtime

        logger.info(f"Loaded {len(profiles)} profiles from {path}: {list(profiles.keys())}")
        return profiles

    except Exception as e:
        logger.error(f"Failed to load profiles from {path}: {e}", exc_info=True)
        return _profiles  # return previous on error


def get_profile(name: str) -> Optional[dict]:
    """Get a profile by name. Returns None if not found."""
    with _lock:
        if not _profiles:
            load_profiles()
        return _profiles.get(name)


def get_all_profiles() -> dict[str, dict]:
    """Get all loaded profiles."""
    with _lock:
        if not _profiles:
            load_profiles()
        return dict(_profiles)


def _sighup_handler(signum, frame):
    """Reload profiles on SIGHUP."""
    logger.info("SIGHUP received — reloading profiles")
    load_profiles()


def install_sighup_handler():
    """Install SIGHUP handler for hot-reloading profiles."""
    if os.name != "nt":  # SIGHUP not available on Windows
        signal.signal(signal.SIGHUP, _sighup_handler)
        logger.info("SIGHUP handler installed for profile hot-reload")
