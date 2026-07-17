"""config.v1 preflight — the shared boot-time deployment-config validator (ADR-0026).

CANONICAL COPY: ``deploy/contracts/config.v1/preflight.py``. Every adopted service vendors this
file VERBATIM as ``config_preflight.py`` next to its ``config.v1.json`` declaration —
``gate:config-contract`` enforces byte-equality — so the module is importable inside every image
without a cross-domain package dependency (P2: the services stay isolated bricks; they share a
contract, not a code distribution).

The three declaration classes (see the contract README):

* **required-explicit** — unset/empty at boot ⇒ :class:`ConfigError` (the deploy refuses to boot
  with ONE message naming every missing key — fail loud, P18/ADR-0010);
* **defaulted** — the documented code default applies; nothing to enforce at boot;
* **capability** — the key belongs to a named capability whose tri-state is computed from the env:
  ``configured`` / ``not_configured`` / ``misconfigured`` (mode=all: some-but-not-all keys set).
  The service RUNS either way; capability-gated endpoints consult :func:`capability_state` and fail
  loud with a typed, actionable error; ``/health`` exposes the rows via :func:`capability_health`
  (ADDITIVE — existing health consumers keep working).

A capability may also declare a **live probe** (contract ``$defs/Probe``): one cheap verification
that the SET values actually work — an authenticated HTTP call (``http``: an unauthorized answer or
a network failure ⇒ misconfigured; any other status ⇒ the credential was accepted) or a credentials
FILE check (``file``: regular readable non-empty JSON ⇒ ok; a directory — docker's bind-mount of a
MISSING host path — or unreadable/non-JSON ⇒ misconfigured). Probes run only when the env-level
state is already ``configured``: once at boot (logged, never boot-blocking) and lazily on ``/health``
when the cached result is older than the probe's ``ttl_s``. This is what turns the silent-401 STT
token and the absent claude-credentials mount into a visible ``misconfigured`` row BEFORE any
meeting/agent runs.

Env-level state is computed AT CALL TIME (no boot-time snapshot), so tests monkeypatching the
environment and long-lived processes both observe the truth.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import List, Mapping, Optional

log = logging.getLogger("config.v1.preflight")

CONFIGURED = "configured"
NOT_CONFIGURED = "not_configured"
MISCONFIGURED = "misconfigured"


class ConfigError(RuntimeError):
    """A deployment-config violation the boot must not survive (fail loud + attributable, P18)."""


@lru_cache(maxsize=1)
def load_declaration() -> dict:
    """This service's config.v1 declaration (``config.v1.json``, vendored next to this module).

    Sanity-checks the declaration's internal references (a ``capability``-classed key must name a
    declared capability) so a broken declaration fails at first use, not silently.
    """
    path = Path(__file__).resolve().parent / "config.v1.json"
    decl = json.loads(path.read_text(encoding="utf-8"))
    caps = decl.get("capabilities") or {}
    for entry in decl.get("keys") or []:
        if entry.get("class") == "capability" and entry.get("capability") not in caps:
            raise ConfigError(
                f"config.v1 declaration for {decl.get('service')!r} is inconsistent: key "
                f"{entry.get('key')!r} names undeclared capability {entry.get('capability')!r}"
            )
    return decl


def _is_set(env: Mapping[str, str], key: str) -> bool:
    # Empty string counts as UNSET: the deploy surfaces default absent vars to "" (compose
    # `${VAR:-}`, lite `export VAR="${VAR:-}"`), so "" and absent must mean the same thing.
    return bool((env.get(key) or "").strip())


def capability_states(env: Optional[Mapping[str, str]] = None) -> dict:
    """The env-level tri-state of every declared capability — PURE (no probe I/O), computed from
    the live env. This is the request-path oracle capability-gated endpoints use."""
    env = os.environ if env is None else env
    decl = load_declaration()
    caps = decl.get("capabilities") or {}
    keys_by_cap: dict = {name: [] for name in caps}
    for entry in decl.get("keys") or []:
        if entry.get("class") == "capability":
            keys_by_cap[entry["capability"]].append(entry["key"])
    states = {}
    for name, cap in caps.items():
        keys = keys_by_cap.get(name) or []
        present = [k for k in keys if _is_set(env, k)]
        if (cap.get("mode") or "all") == "any":
            states[name] = CONFIGURED if present else NOT_CONFIGURED
        elif keys and len(present) == len(keys):
            states[name] = CONFIGURED
        elif not present:
            states[name] = NOT_CONFIGURED
        else:
            states[name] = MISCONFIGURED
    return states


def capability_state(name: str, env: Optional[Mapping[str, str]] = None) -> str:
    """One capability's env-level tri-state. An UNDECLARED name raises — gating on a capability the
    declaration does not carry is a bug, not a runtime condition."""
    states = capability_states(env)
    if name not in states:
        raise ConfigError(
            f"capability {name!r} is not declared in {load_declaration().get('service')!r}'s config.v1"
        )
    return states[name]


def missing_capability_keys(name: str, env: Optional[Mapping[str, str]] = None) -> List[str]:
    """The unset member keys behind a not_configured/misconfigured capability — so a gated
    endpoint's error can name EXACTLY what to set (actionable, not just 'unavailable')."""
    env = os.environ if env is None else env
    decl = load_declaration()
    keys = [
        e["key"]
        for e in decl.get("keys") or []
        if e.get("class") == "capability" and e.get("capability") == name
    ]
    return [k for k in keys if not _is_set(env, k)]


# ── live probes (contract $defs/Probe) — do the SET values actually work? ────────────────────────

_probe_cache: dict = {}


def _reset_probe_cache() -> None:
    """Test seam: forget cached probe results (the cache is per-process, keyed by capability)."""
    _probe_cache.clear()


def _http_probe(spec: dict, env: Mapping[str, str], timeout: float) -> dict:
    """One authenticated request. Unauthorized statuses / network failure ⇒ FAIL (the credential is
    set but does not work); ANY other status (400/404/405/…) proves reachability + auth ⇒ ok."""
    base = (env.get(spec["url_key"]) or "").strip().rstrip("/")
    url = base + (spec.get("path") or "")
    req = urllib.request.Request(url, data=b"", method=(spec.get("method") or "POST"))
    token = (env.get(spec["auth_key"]) or "").strip() if spec.get("auth_key") else ""
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — declared endpoint
            status = int(r.status)
    except urllib.error.HTTPError as e:
        status = int(e.code)
    except Exception as e:  # noqa: BLE001 — a probe must never throw past here
        return {"ok": False, "reason": f"unreachable: {e.__class__.__name__}: {e}"}
    if status in (spec.get("unauthorized_statuses") or [401, 403]):
        return {"ok": False, "status": status,
                "reason": "unauthorized — the configured token was REJECTED by the endpoint"}
    return {"ok": True, "status": status}


def _file_probe(spec: dict, env: Mapping[str, str], timeout: float) -> dict:
    """Verify a credentials file AS VISIBLE TO THIS SERVICE (the key's own path, then any declared
    in-container mirror mounts of a docker-HOST path). A directory here is the signature of docker
    bind-mounting a MISSING host path — exactly the 'Not logged in' worker failure, caught early."""
    raw = (env.get(spec["path_key"]) or "").strip()
    if not raw:
        return {"ok": True, "skipped": f"{spec['path_key']} unset — this credential path is not in use"}
    tried = []
    for p in [raw, *(spec.get("fallback_paths") or [])]:
        path = Path(p)
        if not path.exists():
            tried.append(f"{p}: not found")
            continue
        if not path.is_file():
            return {"ok": False, "path": p,
                    "reason": f"{p} is not a regular file — docker bind-mounts a MISSING host path "
                              f"as a DIRECTORY, so the host file behind {spec['path_key']} is absent"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "path": p, "reason": f"{p}: unreadable or not JSON ({e.__class__.__name__})"}
        if not data:
            return {"ok": False, "path": p, "reason": f"{p}: empty credentials JSON"}
        return {"ok": True, "path": p}
    return {"ok": False, "reason": "; ".join(tried)}


def _run_probe(spec: dict, env: Mapping[str, str]) -> dict:
    timeout = float(spec.get("timeout_s") or 2)
    kind = spec.get("kind")
    if kind == "http":
        return _http_probe(spec["http"], env, timeout)
    if kind == "file":
        return _file_probe(spec["file"], env, timeout)
    return {"ok": False, "reason": f"unknown probe kind {kind!r}"}


def _cached_probe(name: str, spec: dict, env: Mapping[str, str], force: bool = False) -> dict:
    ttl = float(spec.get("ttl_s") or 60)
    now = time.monotonic()
    hit = _probe_cache.get(name)
    if not force and hit is not None and (now - hit["at"]) < ttl:
        return hit["result"]
    result = _run_probe(spec, env)
    _probe_cache[name] = {"at": now, "result": result}
    return result


def capability_health(env: Optional[Mapping[str, str]] = None, force_probe: bool = False) -> dict:
    """The /health rows: env-level tri-state per capability, PLUS the live-probe verdict for
    capabilities that declare one (probe failure demotes the row to ``misconfigured`` with a
    reason). Probe results are cached per the probe's ``ttl_s``; a row's shape is
    ``{"state": ..., "probe"?: {"ok": ..., ...}}`` — additive next to the env-only state."""
    env = os.environ if env is None else env
    decl = load_declaration()
    caps = decl.get("capabilities") or {}
    rows = {}
    for name, state in capability_states(env).items():
        row: dict = {"state": state}
        spec = (caps.get(name) or {}).get("probe")
        if spec and state == CONFIGURED:
            result = _cached_probe(name, spec, env, force=force_probe)
            row["probe"] = result
            if not result.get("ok"):
                row["state"] = MISCONFIGURED
        rows[name] = row
    return rows


def preflight(env: Optional[Mapping[str, str]] = None) -> dict:
    """Boot-time validation of the declaration against the environment.

    Raises :class:`ConfigError` naming EVERY missing required-explicit key (one actionable message,
    not a peel-the-onion loop); runs each configured capability's live probe once; logs one line per
    capability so a deploy's config completeness is visible in the boot log (a failed probe logs a
    WARNING but never blocks boot — capabilities gate endpoints, not the process).
    Returns ``{"service", "capabilities"}`` (the same row shape as :func:`capability_health`).
    """
    env = os.environ if env is None else env
    decl = load_declaration()
    required = [e for e in decl.get("keys") or [] if e.get("class") == "required-explicit"]
    missing = [e for e in required if not _is_set(env, e["key"])]
    if missing:
        detail = "; ".join(f"{e['key']} ({e['description']})" for e in missing)
        raise ConfigError(
            f"{decl.get('service')} is misconfigured and refuses to boot — required environment "
            f"variable(s) not set: {detail}. Each is declared required-explicit in this service's "
            "config.v1 declaration (config.v1.json, gate:config-contract); set them and restart."
        )
    rows = capability_health(env, force_probe=True)
    for name, row in sorted(rows.items()):
        if row["state"] == MISCONFIGURED:
            log.warning("config.v1 capability %s: MISCONFIGURED — %s", name,
                        (row.get("probe") or {}).get("reason", "some-but-not-all keys set"))
        else:
            log.info("config.v1 capability %s: %s", name, row["state"])
    return {"service": decl.get("service"), "capabilities": rows}
