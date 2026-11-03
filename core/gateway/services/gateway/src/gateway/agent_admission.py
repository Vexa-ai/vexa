"""agent_admission.py — which people the agent tier is enabled for (`VEXA_AGENT_ALLOW`).

The agent domain's rows are scoped `bot,tx`, which every ordinary key carries; this setting narrows
the tier to the people a deployment enables it for. The same setting is read by agent-api, and the
two readings must agree, so the rule is spelled once here and held to it by the tests:

  unset or empty    -> no restriction: every caller a scope admits (the behaviour before it existed)
  `*`               -> everyone
  otherwise         -> a comma-separated list of user ids and/or email addresses, compared without
                       case; an entry starting with `@` admits every address at that domain

A caller the list does not name is answered 403 `agent_tier_not_enabled` on every agent-domain row,
before anything is forwarded. A worker's delegation token is resolved by identity to the person it
acts for (`user_id`, `email`), so a worker is admitted exactly when that person is.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import FrozenSet, Optional

from fastapi import Response

ENV = "VEXA_AGENT_ALLOW"
REFUSAL = {"detail": "agent_tier_not_enabled"}


class AgentAllow:
    """The parsed setting. `unrestricted` is true for unset, empty and `*`."""

    def __init__(self, raw: Optional[str]) -> None:
        entries = [e.strip().lower() for e in (raw or "").split(",") if e.strip()]
        self.unrestricted = not entries or "*" in entries
        self.ids: FrozenSet[str] = frozenset(e for e in entries if "@" not in e)
        self.emails: FrozenSet[str] = frozenset(e for e in entries if "@" in e and not e.startswith("@"))
        self.domains: FrozenSet[str] = frozenset(e[1:] for e in entries if e.startswith("@") and len(e) > 1)

    @classmethod
    def from_env(cls) -> "AgentAllow":
        return cls(os.environ.get("VEXA_AGENT_ALLOW"))

    def admits(self, user_data: Mapping) -> bool:
        if self.unrestricted:
            return True
        uid = str(user_data.get("user_id") or "").strip().lower()
        if uid and uid in self.ids:
            return True
        email = str(user_data.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return False
        if email in self.emails:
            return True
        return email.rsplit("@", 1)[1] in self.domains


def refused_response() -> Response:
    return Response(content=json.dumps(REFUSAL), status_code=403, media_type="application/json")
