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
  * a domain that IS configured and does not answer FAILS THE BOOT, by name. "The meetings tools are
    missing" must never be something a person discovers by asking for one.
  * a domain that is NOT configured contributes nothing and is not asked. Its tools are absent, and
    absent is a state an agent recovers from.

DOES NOT ANSWER means exactly that: the connection was refused, the read timed out, or the door
replied 5xx — nothing came back that the domain itself authored. A domain that replies 404 to
`/.well-known/mcp-tools.json` HAS answered; what it said is "I serve no manifest", which is the
ordinary state of a deployed domain that has not published one yet, and it contributes no tools
without failing anything. The two are not the same event and only one of them is silence.

AND SILENCE IS WAITED OUT FIRST. A cold `compose up` starts every service at once, so the edge can
reach a domain's port before that domain is listening — which is a race, not a deployment fault, and
failing on the first refused connection would turn every cold start into a coin flip. The probe
retries :data:`DEFAULT_ATTEMPTS` times, :data:`DEFAULT_PAUSE_SECONDS` apart, and only then refuses.
Both are operator-tunable (:data:`ATTEMPTS_ENV`, :data:`PAUSE_ENV`); the suite sets the pause to
zero so the retry path is exercised without the wait.
"""
from __future__ import annotations

import os
import time
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

#: How long boot waits for a configured domain to start answering, and the env keys that move it.
#: Five attempts two seconds apart is ~8s of patience — longer than a compose race, far shorter than
#: an orchestrator's restart backoff, so a domain that is genuinely down still fails fast.
ATTEMPTS_ENV = "VEXA_MCP_BOOT_PROBE_ATTEMPTS"
PAUSE_ENV = "VEXA_MCP_BOOT_PROBE_PAUSE_SECONDS"
DEFAULT_ATTEMPTS = 5
DEFAULT_PAUSE_SECONDS = 2.0
#: Per-attempt read timeout. A domain that accepts the connection and then says nothing is silent in
#: the sense that matters, and it must not hold the boot open.
PROBE_TIMEOUT_S = 5


class DomainSilent(Exception):
    """A configured domain that never answered at all — internal to this module.

    Carries what the last attempt saw, because "connection refused" and "HTTP 502" send an operator
    to different places and the difference is gone by the time it becomes a boot message.
    """

    def __init__(self, url: str, attempts: int, last: str) -> None:
        super().__init__(f"{attempts} attempts at {url}, last: {last or 'no response'}")
        self.url, self.attempts, self.last = url, attempts, last


def _number(value: object, fallback: float) -> float:
    """An operator's override, or the default when it is missing or not a usable number."""
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return out if out >= 0 else fallback


def _probe(client: httpx.Client, url: str, *, attempts: int, pause: float) -> Optional[dict]:
    """One boot fetch, with the cold-start race waited out.

    Returns the JSON document when the door served one, and ``None`` when the door ANSWERED and had
    none to serve (any non-200, or a 200 this cannot parse). Raises :class:`DomainSilent` when
    nothing the domain authored ever came back.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            r = client.get(url, timeout=PROBE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — refused, timed out, DNS: all the same silence
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code < 500:
                # AN ANSWER. 200 with a document is the document; anything else is the domain
                # saying it serves nothing here, which is a state, not a fault.
                if r.status_code != 200:
                    return None
                try:
                    return r.json()
                except Exception:  # noqa: BLE001 — a 200 that is not JSON serves no manifest
                    return None
            last = f"HTTP {r.status_code}"
        if attempt < attempts and pause:
            time.sleep(pause)
    raise DomainSilent(url, attempts, last)


def _silent(domain: str, base: str, what: str, silent: DomainSilent) -> ManifestError:
    return ManifestError(
        f"{domain} is configured ({base}) but never answered its {what} — {silent}. Refusing to "
        f"boot with {domain}'s tools silently absent: a deployment that names a domain is promising "
        "its tools, and an agent handed a short tools/list has no way to know one is missing.")


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
    """A single attempt, silence rendered as ``None`` — kept for callers that are not the boot.

    The boot itself uses :func:`_probe`, which distinguishes "answered, nothing here" from "never
    answered": collapsing those two into ``None`` is precisely the bug this module had.
    """
    try:
        return _probe(client, url, attempts=1, pause=0)
    except DomainSilent:
        return None


def discover(client: httpx.Client, *, env: Optional[dict] = None
             ) -> Tuple[Assembly, Dict[str, dict], Dict[str, str]]:
    """``(assembly, openapi_by_domain, base_urls)``. Raises :class:`ManifestError` on a boot rule.

    SYNCHRONOUS on purpose. This runs once, at boot, and every rule it applies is one that must stop
    the process rather than degrade a request — so it belongs before the server is listening, not in
    an async startup hook racing the first caller."""
    env = env if env is not None else os.environ
    attempts = max(1, int(_number(env.get(ATTEMPTS_ENV), DEFAULT_ATTEMPTS)))
    pause = _number(env.get(PAUSE_ENV), DEFAULT_PAUSE_SECONDS)
    bases = deployed_domains(env)
    manifests, openapi = [], {}
    for domain, base in bases.items():
        try:
            doc = _probe(client, f"{base}{MANIFEST_PATH}", attempts=attempts, pause=pause)
        except DomainSilent as silent:
            # THE FAIL DIRECTION THIS MODULE'S DOCSTRING PROMISES. Configured and never answering is
            # a whole domain's tools gone, and gone quietly — the one outcome nobody can diagnose
            # from the outside, because a short tools/list looks exactly like a small deployment.
            raise _silent(domain, base, "manifest", silent) from silent
        if doc is None:
            # ANSWERED, and what it said was "no manifest here". A domain may legitimately carry
            # none yet — it simply contributes no tools. Silence is the case above.
            continue
        manifests.append(doc)
        try:
            spec = _probe(client, f"{base}{OPENAPI_PATH}", attempts=attempts, pause=pause)
        except DomainSilent as silent:
            raise _silent(domain, base, "OpenAPI", silent) from silent
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
        try:
            spec = _probe(client, f"{base}{OPENAPI_PATH}", attempts=attempts, pause=pause)
        except DomainSilent as silent:
            raise _silent(str(domain), base, "OpenAPI", silent) from silent
        if spec is None:
            raise ManifestError(f"{domain} (mounted): no OpenAPI at {base}{OPENAPI_PATH}")
        openapi[domain] = spec

    deployed: Set[str] = set(bases)
    # `env` reaches the assembler because "can this edge authenticate that tool" is a DEPLOYMENT
    # question, and the assembler is where every other boot refusal already lives.
    return assemble(manifests, deployed=deployed, env=env), openapi, bases
