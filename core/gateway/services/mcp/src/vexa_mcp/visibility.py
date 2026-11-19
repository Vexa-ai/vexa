"""visibility.py — which tools a caller is SHOWN in ``tools/list`` and may CALL (``VEXA_MCP_TOOLS_*``).

An upgrade adds tools. A deployment that promises its existing users an unchanged surface needs to
hold the new ones back from them while the people it names try them first. Three settings, read
once at boot:

  ``VEXA_MCP_TOOLS_ALLOW``   comma list of tool names an ordinary caller is shown. Unset or empty:
                             every registered tool (no filter — the behaviour before it existed).
  ``VEXA_MCP_TOOLS_DENY``    comma list of tool names nobody is shown, the cohort included.
  ``VEXA_MCP_TOOLS_COHORT``  the people shown every registered tool regardless of the allow-list
                             (the deny-list still holds): user ids and/or email addresses, compared
                             without case; ``@domain`` admits every address there; ``*`` everyone.
                             The grammar of ``VEXA_AGENT_ALLOW``. Unset or empty: nobody.

A tool a caller is not shown is also not callable by that caller: ``tools/call`` answers exactly what
it answers for a tool that does not exist (``Unknown tool: <name>``), and nothing is forwarded.

WHO THE CALLER IS comes from the identity the gateway stamped on the ``/mcp`` request
(``x-user-id``, ``x-user-email``, gateway-identity.v1). The gateway strips any such header a client
sent, and this service is reachable only behind the gateway. A request without them is an ordinary
caller, never a cohort member.

SHARED CALLERS (D3, default-deny for shared tokens, 0.13.4). A caller whose delegation is shared —
a room's turn or a shared chat's (``x-user-delegation-shared: 1``, or a workspace principal
``x-user-principal-kind: workspace``, both stamped by the gateway from the signed delegation) —
is shown, and may call, ONLY the tools whose domain manifest declares ``"shared": "allow"`` (agent-api
stamps it from its ``routes.v1.json``, the same table its own gate enforces). A tool that declares
nothing is refused to such a caller, so a new tool, or a domain that has not classified its tools,
reaches no room. This holds whatever the three settings say, the cohort included, and is always on.

This is a surface control, not an authorization boundary: every tool still acts through the gateway
with the caller's own credential, and the gateway's scopes and ``VEXA_AGENT_ALLOW`` decide what the
credential may do. What this adds is that an existing key's tool list does not change on upgrade.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet, Iterable, Mapping, Optional

import mcp.types as mcp_types

ALLOW_ENV = "VEXA_MCP_TOOLS_ALLOW"
DENY_ENV = "VEXA_MCP_TOOLS_DENY"
COHORT_ENV = "VEXA_MCP_TOOLS_COHORT"

logger = logging.getLogger(__name__)


SHARED_HEADER = "x-user-delegation-shared"
PRINCIPAL_KIND_HEADER = "x-user-principal-kind"


def is_shared_caller(headers: Mapping[str, str]) -> bool:
    """A caller whose answer other people read: a shared delegation, or a workspace principal (D2)."""
    if str(headers.get(SHARED_HEADER) or "").strip() == "1":
        return True
    return str(headers.get(PRINCIPAL_KIND_HEADER) or "").strip() == "workspace"


def _names(raw: Optional[str]) -> FrozenSet[str]:
    return frozenset(n.strip() for n in str(raw or "").split(",") if n.strip())


@dataclass(frozen=True)
class Policy:
    allow: FrozenSet[str]          # empty = no allow-list
    deny: FrozenSet[str]
    cohort_all: bool
    cohort_ids: FrozenSet[str]
    cohort_emails: FrozenSet[str]
    cohort_domains: FrozenSet[str]
    #: D3: the names a shared caller may be shown, asked at every check (late domains join later).
    #: None = nothing is declared shareable, so a shared caller is shown nothing.
    shared_source: Optional[Callable[[], Iterable[str]]] = field(default=None, compare=False)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None,
                 shared_source: Optional[Callable[[], Iterable[str]]] = None) -> "Policy":
        env = os.environ if env is None else env
        entries = [e.lower() for e in _names(env.get(COHORT_ENV))]
        return cls(
            allow=_names(env.get(ALLOW_ENV)),
            deny=_names(env.get(DENY_ENV)),
            cohort_all="*" in entries,
            cohort_ids=frozenset(e for e in entries if "@" not in e and e != "*"),
            cohort_emails=frozenset(e for e in entries if "@" in e and not e.startswith("@")),
            cohort_domains=frozenset(e[1:] for e in entries if e.startswith("@") and len(e) > 1),
            shared_source=shared_source,
        )

    @property
    def active(self) -> bool:
        return bool(self.allow or self.deny)

    def in_cohort(self, headers: Mapping[str, str]) -> bool:
        uid = str(headers.get("x-user-id") or "").strip().lower()
        email = str(headers.get("x-user-email") or "").strip().lower()
        if not uid and not email:
            return False
        if self.cohort_all:
            return True
        if uid and uid in self.cohort_ids:
            return True
        if email and "@" in email:
            return email in self.cohort_emails or email.rsplit("@", 1)[1] in self.cohort_domains
        return False

    def shared_allowed(self) -> FrozenSet[str]:
        try:
            return frozenset(self.shared_source()) if self.shared_source is not None else frozenset()
        except Exception:  # noqa: BLE001 — a table that cannot be read shows a shared caller nothing
            logger.warning("mcp shared-tool table unreadable; a shared caller is shown no tool")
            return frozenset()

    def shown(self, name: str, headers: Mapping[str, str]) -> bool:
        if name in self.deny:
            return False
        if is_shared_caller(headers) and name not in self.shared_allowed():
            return False
        if not self.allow or name in self.allow:
            return True
        return self.in_cohort(headers)


def _caller_headers(server: Any) -> Mapping[str, str]:
    """The ``/mcp`` HTTP request's headers for the message being served (empty when there is none)."""
    try:
        request = getattr(server.request_context, "request", None)
    except LookupError:
        return {}
    headers = getattr(request, "headers", None)
    return headers if headers is not None else {}


def install(mcp: Any, env: Optional[Mapping[str, str]] = None,
            shared_source: Optional[Callable[[], Iterable[str]]] = None) -> Policy:
    """Filter ``tools/list`` and refuse ``tools/call`` per caller. Always installed: a shared caller
    (D3) is filtered whatever the settings say; with nothing set, nobody else is.

    Install it INNERMOST on the call path (before the other call wrappers), so a refused call takes
    every later step exactly as a call to a tool that does not exist does."""
    policy = Policy.from_env(env, shared_source=shared_source)
    if not policy.active:
        logger.info("mcp tool visibility: no allow/deny set; shared callers filtered (D3)")
    registered = [t.name for t in mcp.tools]
    unknown = sorted((policy.allow | policy.deny) - set(registered))
    if unknown and policy.active:
        # A name this deployment does not serve (a domain not deployed, a feature off, a typo).
        # Logged rather than fatal: the surface legitimately differs between deployments.
        logger.warning("mcp tool visibility names tools this deployment does not serve: %s",
                       ", ".join(unknown))
    if policy.active:
        logger.info("mcp tool visibility on: allow=%d deny=%d", len(policy.allow), len(policy.deny))

    server = mcp.server
    list_original = server.request_handlers.get(mcp_types.ListToolsRequest)
    call_original = server.request_handlers.get(mcp_types.CallToolRequest)

    if list_original is not None:
        async def list_handler(req):
            result = await list_original(req)
            headers = _caller_headers(server)
            inner = getattr(result, "root", result)
            tools = getattr(inner, "tools", None)
            if isinstance(tools, list):
                inner.tools = [t for t in tools if policy.shown(t.name, headers)]
            return result
        server.request_handlers[mcp_types.ListToolsRequest] = list_handler

    if call_original is not None:
        async def call_handler(req: mcp_types.CallToolRequest):
            name = req.params.name
            if not policy.shown(name, _caller_headers(server)):
                # The library's own answer for a tool it does not have, word for word.
                return server._make_error_result(f"Unknown tool: {name}")
            return await call_original(req)
        server.request_handlers[mcp_types.CallToolRequest] = call_handler

    return policy


def shown_names(policy: Policy, names: Iterable[str], headers: Mapping[str, str]) -> list:
    """``names`` as ``headers``' caller is shown them, order kept (for other surfaces and tests)."""
    return [n for n in names if policy.shown(n, headers)]
