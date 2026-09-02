"""Where this edge lives, and what it is allowed to know.

THE MCP IS AN EDGE, not a domain and not a client (PRD decision 40.1, founder ruling 2026-09-02).
Like the gateway it exposes the domains with the caller's identity attached and owns no state of its
own, and like the gateway it is ONE SERVICE IN THE STACK: its own container, its siblings named by
URL, fronted at ``/mcp``. A person's Claude Code or Codex connects to it over streamable HTTP with
their own token. It does not run on a laptop.

ONE SHAPE OF DEPLOYMENT INPUT: a sibling service's URL, or a credential. There is no docker socket
here, no container name, no database URL and no path to another service's source tree — the four
things that made the rig un-packageable (seam inventory B6). Every key below is declared in
``core/mcp/config.v1.json`` and checked by ``gate:config-contract``.

Config is env, service-shaped, exactly like every other service in the stack: no dotfile, no
per-person file, nothing read out of a home directory. ``VEXA_HOME`` is the one path here and it is
not config — it is where this process keeps the small amount of state it holds (durable tokens,
pending sign-in codes, the per-user gateway keys it minted), a volume in a deployment.
"""
from __future__ import annotations

import os
import pathlib


def get(key: str, default: str = "") -> str:
    """One deployment value, from the environment. Empty is the same as unset — a key set to the
    empty string in a compose file means "I did not configure this", never "configure it to
    nothing"."""
    val = os.environ.get(key)
    return val if val not in (None, "") else default


# ── the siblings ─────────────────────────────────────────────────────────────────────────────
# VEXA_URL is the stack's gateway — the one URL a deployment must name; the rest default to the
# in-network service names, which is what a compose or helm deployment actually has.
URL = get("VEXA_URL", "http://gateway:8000").rstrip("/")
GATEWAY = get("VEXA_GATEWAY_URL", URL).rstrip("/")
AGENT_API = get("VEXA_AGENT_API_URL", "http://agent-api:8100").rstrip("/")
ADMIN_API = get("VEXA_ADMIN_API_URL", "http://admin-api:8001").rstrip("/")
FLOWS_API = get("VEXA_FLOWS_API_URL", "http://flows-api:8200").rstrip("/")
MAILPIT = get("MAILPIT_URL", "").rstrip("/")
UI_BASE = get("VEXA_UI_URL", "").rstrip("/")
CANONICAL = get("VEXA_PUBLIC_MCP_URL", "http://localhost:18310/mcp")

# ── credentials ──────────────────────────────────────────────────────────────────────────────
# The platform admin token, AS A DEPLOYMENT VALUE. The rig lifted this out of another container's
# environment with `docker inspect vexa-dogfood-admin-api-1` — a docker socket and a hardcoded
# container name, for a string. It is a credential; it arrives the way credentials arrive.
ADMIN_API_TOKEN = get("VEXA_ADMIN_API_TOKEN", "")
FLOWS_API_KEY = get("VEXA_FLOWS_API_KEY", "")
DELEGATION_SECRET = get("VEXA_MCP_DELEGATION_SECRET", "")
# The same internal secret the rest of the stack's internal tier checks (#526), under the same
# name, from the same contract. A server-to-server caller presents it instead of a person's token.
INTERNAL_API_SECRET = get("VEXA_INTERNAL_API_SECRET", "")

# ── behaviour ────────────────────────────────────────────────────────────────────────────────
# The default regime for a workspace nothing has been recorded about. `cloud` = the files live on
# the stack and the workspace verbs read and write them there. `local` = the files live on the
# person's own machine and NO cloud agent runs for them: the workspace verbs still operate on the
# cloud copy, git (workspace_pull / workspace_push) is the sync, and the person's own agent writes
# the local files itself with its native tools.
WORKSPACE_REGIME = (get("VEXA_WORKSPACE_REGIME", "cloud") or "cloud").strip().lower()
# The `token=` call-argument fallback and the `GET /do` bridge put a credential in a query string.
# Right for a fetch-only agent on a private host, wrong anywhere requests are logged — so unlike
# the rig, which defaulted it ON, the shipped default is OFF and the dogfood lane opts in.
RIG_MODE = get("VEXA_RIG_MODE", "0") != "0"
MAIL_SMTP_HOST = get("VEXA_MAIL_SMTP_HOST", "localhost")
MAIL_SMTP_PORT = int(get("VEXA_MAIL_SMTP_PORT", "1025") or "1025")
MAIL_ADDR = get("VEXA_MAIL_ADDR", "")
DOCS_BASE = get("VEXA_DOCS_URL", "https://docs.vexa.ai").rstrip("/")
PORT = int(get("PORT", "18310") or "18310")

# ── the state this edge keeps, all of it under one directory ─────────────────────────────────
# NOT config: a volume. Durable `vxa_mcp_…` tokens, pending sign-in codes, the one gateway key per
# person this process minted. `tests/test_thin_forward.py` asserts no tool writes anywhere else.
VEXA_HOME = pathlib.Path(get("VEXA_HOME", "") or (pathlib.Path.home() / ".vexa"))
TOKENS_FILE = VEXA_HOME / "mcp-tokens.json"
USER_KEYS_FILE = VEXA_HOME / "user-api-keys.json"
EMAIL_CODES = VEXA_HOME / "oauth" / "email-codes.json"
LOGINS = VEXA_HOME / "oauth" / "logins.json"
REGIMES = VEXA_HOME / "oauth" / "regimes.json"
REVOKED_FILE = VEXA_HOME / "mcp-delegation-revoked.json"
FRICTION_LOG = VEXA_HOME / "friction.jsonl"
CAPS_DIR = VEXA_HOME / "caps"

LOGIN_TTL = 900
