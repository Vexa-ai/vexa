"""GENERATED from core/agent/contracts/unit.v1/unit.schema.json by gen-faults.mjs — DO NOT EDIT.
Regenerate with: node core/agent/contracts/unit.v1/gen-faults.mjs

The typed-fault vocabulary (unit.v1 ``Fault``): every ``source`` a fault may name — WHO failed — and, per
source, every ``kind`` — HOW. One class per source holds its ``SOURCE``, one constant per kind and
``KINDS``. Generated with the same text into core/agent/llm/fault_wire.py and
core/agent/shared/fault_wire.py (llm/ imports nothing from product code), and into the terminal's
clients/terminal/src/surfaces/faultWire.ts.
"""
from __future__ import annotations

from typing import Dict, Tuple

SOURCES: Tuple[str, ...] = ("runtime", "model-provider", "vexa-tools", "agent-worker", "agent-api", "gateway")


class Runtime:
    """source "runtime" and its kinds."""

    SOURCE = "runtime"
    SPAWN_REFUSED = "spawn_refused"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNAUTHORIZED = "unauthorized"
    REFUSED = "refused"
    NOT_FOUND = "not_found"
    UNREACHABLE = "unreachable"
    UNAVAILABLE = "unavailable"
    BAD_RESPONSE = "bad_response"
    KINDS: Tuple[str, ...] = ("spawn_refused", "quota_exceeded", "unauthorized", "refused", "not_found", "unreachable", "unavailable", "bad_response")


class ModelProvider:
    """source "model-provider" and its kinds."""

    SOURCE = "model-provider"
    UNPAID = "unpaid"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    REFUSED = "refused"
    NO_TOOL_CALLING = "no_tool_calling"
    KINDS: Tuple[str, ...] = ("unpaid", "unauthorized", "rate_limited", "unavailable", "refused", "no_tool_calling")


class VexaTools:
    """source "vexa-tools" and its kinds."""

    SOURCE = "vexa-tools"
    ACCESS_EXPIRED = "access_expired"
    KINDS: Tuple[str, ...] = ("access_expired",)


class AgentWorker:
    """source "agent-worker" and its kinds."""

    SOURCE = "agent-worker"
    TOOLS_UNCONFINED = "tools_unconfined"
    CREDENTIAL_CONFLICT = "credential_conflict"
    KINDS: Tuple[str, ...] = ("tools_unconfined", "credential_conflict")


class AgentApi:
    """source "agent-api" and its kinds."""

    SOURCE = "agent-api"
    INTERNAL = "internal"
    UNAVAILABLE = "unavailable"
    KINDS: Tuple[str, ...] = ("internal", "unavailable")


class Gateway:
    """source "gateway" and its kinds."""

    SOURCE = "gateway"
    UNREACHABLE = "unreachable"
    KINDS: Tuple[str, ...] = ("unreachable",)


#: source → its closed kind vocabulary.
KINDS: Dict[str, Tuple[str, ...]] = {
    Runtime.SOURCE: Runtime.KINDS,
    ModelProvider.SOURCE: ModelProvider.KINDS,
    VexaTools.SOURCE: VexaTools.KINDS,
    AgentWorker.SOURCE: AgentWorker.KINDS,
    AgentApi.SOURCE: AgentApi.KINDS,
    Gateway.SOURCE: Gateway.KINDS,
}
