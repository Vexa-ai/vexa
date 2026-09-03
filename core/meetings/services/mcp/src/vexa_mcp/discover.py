"""DISCOVERY — asking the deployed domains what they serve, at startup.

The manifests are not baked into this image. Each domain serves its own at
``/.well-known/mcp-tools.json``, and its OpenAPI beside it, so the surface this edge presents is the
surface the RUNNING builds actually have. A deployment cannot advertise a tool the service behind it
does not serve, because the service is the one that said it did.

WHICH DOMAINS EXIST is a deployment fact, read from the environment: a domain whose base URL is set
is deployed. That is the whole of the eight-configuration story — identity plus any subset of
meetings, flows and agent — and it needs no profile name, because a profile name is a second place
to say something the URLs already say.

FAIL DIRECTIONS, both deliberate and opposite:
  * a domain that IS configured and does not answer FAILS THE BOOT. "The meetings tools are missing"
    must never be something a person discovers by asking for one.
  * a domain that is NOT configured contributes nothing and is not asked. Its tools are absent, and
    absent is a state an agent recovers from.
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Set, Tuple

import httpx

from .manifest import Assembly, ManifestError, assemble, load_mounted

#: domain -> the env key naming its base URL. Identity is always required; the other three are the
#: subset this deployment carries.
DOMAIN_URL_ENV = {
    "identity": "ADMIN_API_URL",
    "meetings": "MEETING_API_URL",
    "flows": "FLOWS_API_URL",
    "agent": "AGENT_API_URL",
}
MANIFEST_PATH = "/.well-known/mcp-tools.json"
OPENAPI_PATH = "/openapi.json"
MOUNT_DIR_ENV = "VEXA_MCP_MANIFEST_DIR"


def deployed_domains(env: Optional[dict] = None) -> Dict[str, str]:
    """``{domain: base_url}`` for every domain this deployment names. Nothing else is asked."""
    env = env if env is not None else os.environ
    out = {}
    for domain, key in DOMAIN_URL_ENV.items():
        url = (env.get(key) or "").strip().rstrip("/")
        if url:
            out[domain] = url
    return out


def fetch(client: httpx.Client, url: str) -> Optional[dict]:
    try:
        r = client.get(url, timeout=5)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001 — an unreachable domain is decided by the caller, not here
        return None


def discover(client: httpx.Client, *, env: Optional[dict] = None
             ) -> Tuple[Assembly, Dict[str, dict], Dict[str, str]]:
    """``(assembly, openapi_by_domain, base_urls)``. Raises :class:`ManifestError` on a boot rule.

    SYNCHRONOUS on purpose. This runs once, at boot, and every rule it applies is one that must stop
    the process rather than degrade a request — so it belongs before the server is listening, not in
    an async startup hook racing the first caller."""
    env = env if env is not None else os.environ
    bases = deployed_domains(env)
    manifests, openapi = [], {}
    for domain, base in bases.items():
        doc = fetch(client, f"{base}{MANIFEST_PATH}")
        if doc is None:
            # A domain may legitimately carry no manifest yet — it simply contributes no tools.
            # What it may NOT do is carry one and be unreachable, which is the case below.
            continue
        manifests.append(doc)
        spec = fetch(client, f"{base}{OPENAPI_PATH}")
        if spec is None:
            raise ManifestError(
                f"{domain} published a manifest but its OpenAPI at {base}{OPENAPI_PATH} did not "
                "answer — refusing to bind a tool blind")
        openapi[domain] = spec

    for doc in load_mounted(env.get(MOUNT_DIR_ENV)):
        domain = doc.get("domain")
        base = (env.get(f"{str(domain).upper()}_API_URL") or "").strip().rstrip("/")
        if not base:
            raise ManifestError(
                f"a mounted manifest for {domain!r} is present but {str(domain).upper()}_API_URL "
                "is unset — a private domain that cannot be reached is a tool list that lies")
        bases[domain] = base
        manifests.append(doc)
        spec = fetch(client, f"{base}{OPENAPI_PATH}")
        if spec is None:
            raise ManifestError(f"{domain} (mounted): no OpenAPI at {base}{OPENAPI_PATH}")
        openapi[domain] = spec

    deployed: Set[str] = set(bases)
    return assemble(manifests, deployed=deployed), openapi, bases
