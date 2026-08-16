"""Environment → a ``Settings`` struct, read once at boot (the keys are declared in config.v1.json).

Two of these keys carry the whole v0 product decision and deserve their names spelled out:

* ``MAILROOM_WORKSPACE_MAP`` — ``address=workspace_id`` pairs, comma-separated. **This map IS the
  workspace resolution**: an invitation addressed to a listed address belongs to that workspace,
  and one addressed to anything else belongs to nobody. v0 configures a single dev pair
  (``mk-dev@dev.vexa.ai``); the multi-workspace story is this value getting longer, not new code.
* ``MAILROOM_API_KEY`` — the Vexa API key the mailroom presents to the public API. The planned
  meetings it creates are that key's meetings, which is how the mailbox becomes an actor in the
  system rather than a privileged internal path.

``MAILROOM_DRY_RUN`` exists for the first live smoke: poll and parse, decide everything, and call
no control-plane mutation. It answers "what WOULD this mailbox have done" without creating rows.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def parse_workspace_map(raw: Optional[str]) -> dict[str, str]:
    """``"a@x=ws-1, b@x=ws-2"`` → ``{"a@x": "ws-1", "b@x": "ws-2"}`` (blank/─malformed pairs skipped)."""
    out: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        address, _, workspace = chunk.partition("=")
        address, workspace = address.strip().lower(), workspace.strip()
        local, _, domain = address.partition("@")
        if local and domain and workspace:
            out[address] = workspace
    return out


@dataclass(frozen=True)
class Settings:
    mailpit_url: str = "http://mailpit:8025"
    meeting_api_url: str = "http://gateway:8000"
    api_key: str = ""
    workspaces: dict[str, str] = field(default_factory=dict)
    internal_secret: str = ""
    state_path: str = "/data/mailroom-state.json"
    poll_interval_s: float = 30.0
    batch_limit: int = 50
    auto_join: bool = True
    dry_run: bool = False
    host: str = "0.0.0.0"
    port: int = 8030
    log_level: str = "info"

    @property
    def configured(self) -> bool:
        """True when the poller can actually run (a mailbox, a key, and at least one workspace)."""
        return bool(self.mailpit_url and self.api_key and self.workspaces)


def settings_from_env(env: Optional[dict] = None) -> Settings:
    e = dict(os.environ if env is None else env)
    workspaces = parse_workspace_map(e.get("MAILROOM_WORKSPACE_MAP"))
    # Single-pair shorthand — the dev deployment sets exactly one address.
    address, workspace = e.get("MAILROOM_WORKSPACE_ADDRESS"), e.get("MAILROOM_WORKSPACE_ID")
    if address and workspace:
        workspaces.setdefault(address.strip().lower(), workspace.strip())
    return Settings(
        mailpit_url=e.get("MAILPIT_URL") or "http://mailpit:8025",
        meeting_api_url=e.get("MEETING_API_URL") or "http://gateway:8000",
        api_key=e.get("MAILROOM_API_KEY") or "",
        internal_secret=e.get("MAILROOM_INTERNAL_SECRET") or "",
        workspaces=workspaces,
        state_path=e.get("MAILROOM_STATE_PATH") or "/data/mailroom-state.json",
        poll_interval_s=float(e.get("MAILROOM_POLL_INTERVAL_S") or 30),
        batch_limit=int(e.get("MAILROOM_BATCH_LIMIT") or 50),
        auto_join=_bool(e.get("MAILROOM_AUTO_JOIN"), True),
        dry_run=_bool(e.get("MAILROOM_DRY_RUN"), False),
        host=e.get("HOST") or "0.0.0.0",
        port=int(e.get("PORT") or 8030),
        log_level=(e.get("LOG_LEVEL") or "info").lower(),
    )
