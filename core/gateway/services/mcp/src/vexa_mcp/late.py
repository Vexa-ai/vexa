"""late.py — an OPTIONAL domain that is down at boot joins the surface when it answers.

Identity is the one domain the edge cannot start without. Every tool provider — meetings, flows,
agent, crm — that is down, slow, or (meetings) answers with no manifest because an older pod is still
rolling must not leave the surface short until the next restart. `discover` records such a domain as PENDING
(logged, by name) instead of failing, the edge boots and serves everything else, and this module
asks each pending domain again every ``VEXA_MCP_REFRESH_SECONDS`` (default 30). When one answers,
its tools are assembled, bound and registered exactly as at boot, the MCP tool list is re-derived,
and every rule that applies at boot applies here too — the visibility allow-list included, since it
filters by name at call time. Readiness never waits on a pending domain: the edge goes ready with
what answered and SELF-HEALS within one refresh interval (30 s) once the domain answers.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi.openapi.utils import get_openapi

from . import bind as bind_mod
from . import discover as discover_mod
from . import features as features_mod
from . import register as register_mod
from .manifest import ManifestError, assemble

logger = logging.getLogger(__name__)

REFRESH_ENV = "VEXA_MCP_REFRESH_SECONDS"
DEFAULT_REFRESH_SECONDS = 30.0


def rederive_tools(mcp: Any) -> None:
    """Re-read this app's routes into ``mcp.tools`` / ``mcp.operation_map`` — the same derivation
    ``FastApiMCP.setup_server`` made at boot — and close every argument schema again. The list and
    call handlers read both attributes per request, so a new route is a new tool from here on."""
    from fastapi_mcp.openapi.convert import convert_openapi_to_mcp_tools

    app = mcp.fastapi
    schema = get_openapi(title=app.title, version=app.version, openapi_version=app.openapi_version,
                         description=app.description, routes=app.routes)
    all_tools, op_map = convert_openapi_to_mcp_tools(
        schema, describe_all_responses=mcp._describe_all_responses,
        describe_full_response_schema=mcp._describe_full_response_schema)
    tools = mcp._filter_tools(all_tools, schema)
    for tool in tools:
        s = getattr(tool, "inputSchema", None)
        if isinstance(s, dict) and s.get("type") == "object":
            s.setdefault("additionalProperties", False)
    mcp.operation_map = op_map
    mcp.tools = tools


class LateDomains:
    def __init__(self, app, mcp, *, pending: Dict[str, str], bases: Dict[str, str], env: dict,
                 features_env, client: Callable[[], httpx.Client],
                 transport: Optional[httpx.AsyncBaseTransport], gateway_url: str) -> None:
        self._app, self._mcp, self._pending, self._bases = app, mcp, dict(pending), dict(bases)
        self._env, self._features_env, self._client = env, features_env, client
        self._transport, self._gateway_url = transport, gateway_url
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def pending(self) -> Dict[str, str]:
        return dict(self._pending)

    def poll_once(self) -> list:
        """Ask every pending domain once. Returns the domains whose tools joined."""
        joined = []
        with self._lock:
            for domain, base in list(self._pending.items()):
                try:
                    with self._client() as client:
                        doc = discover_mod._probe(client, f"{base}{discover_mod.MANIFEST_PATH}",
                                                  attempts=1, pause=0)
                        if doc is None and domain in discover_mod.RETRY_WHEN_EMPTY:
                            continue          # meetings always has one: an old pod answered; ask again
                        if doc is None:
                            # answered: no manifest here. It contributes nothing; stop asking.
                            self._pending.pop(domain, None)
                            logger.info("mcp: %s answered with no manifest; no tools from it", domain)
                            continue
                        spec = discover_mod._probe(client, f"{base}{discover_mod.OPENAPI_PATH}",
                                                   attempts=1, pause=0)
                    if spec is None:
                        raise ManifestError(f"{domain}: manifest without an OpenAPI")
                    deployed = set(self._bases) | {domain}
                    assembly = assemble([doc], deployed=deployed, env=self._env)
                    features_mod.drop_switched_off_domains(assembly, self._features_env)
                    taken = {getattr(r, "operation_id", None) for r in self._app.router.routes}
                    clash = sorted(t.name for t in assembly.tools if t.name in taken)
                    if clash:
                        raise ManifestError(f"{domain} claims tool names already served: {clash}")
                    bound = bind_mod.verify(assembly, {domain: spec})
                    register_mod.register(self._app, bound, {domain: base}, transport=self._transport,
                                          env=self._env, gateway_url=self._gateway_url)
                    rederive_tools(self._mcp)
                    current = getattr(self._app.state, "assembly", None)
                    if current is not None:
                        current.tools.extend(assembly.tools)
                        for t in assembly.tools:
                            current.by_domain[t.domain] = current.by_domain.get(t.domain, 0) + 1
                    self._bases[domain] = base
                    self._pending.pop(domain, None)
                    joined.append(domain)
                    logger.warning("mcp: %s answered; its %d tool(s) joined the surface",
                                   domain, len(assembly.tools))
                except discover_mod.DomainSilent:
                    continue          # still down; asked again next round
                except ManifestError as exc:
                    logger.error("mcp: %s answered but its tools cannot be served: %s", domain, exc)
                except Exception as exc:  # noqa: BLE001 — the refresher outlives a bad round
                    logger.error("mcp: late %s refresh failed (%s)", domain, type(exc).__name__)
        return joined

    def start(self) -> None:
        if self._thread is not None or not self._pending:
            return
        interval = discover_mod._number(self._env.get(REFRESH_ENV), DEFAULT_REFRESH_SECONDS) or DEFAULT_REFRESH_SECONDS

        def _loop() -> None:
            while self._pending and not self._stop.wait(interval):
                self.poll_once()

        self._thread = threading.Thread(target=_loop, daemon=True, name="mcp-late-domains")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
