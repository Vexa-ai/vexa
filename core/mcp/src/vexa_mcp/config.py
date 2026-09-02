"""Where this edge lives, and what it is allowed to know.

THE MCP IS AN EDGE, not a domain and not a client (PRD decision 40.1, founder ruling 2026-09-02).
Like the gateway it exposes the domains with the caller's identity attached and owns no state of its
own, and like the gateway it is ONE SERVICE IN THE STACK: its own container, its siblings named by
URL, fronted at ``/mcp``. A person's Claude Code or Codex connects to it over streamable HTTP with
their own token. It does not run on a laptop.

ONE SHAPE OF DEPLOYMENT INPUT: a sibling service's URL, or a credential. There is no docker socket
here, no container name, no database URL and no path to another service's source tree — the four
things that made the rig un-packageable (seam inventory B6). Config is env, service-shaped, exactly
like every other service in the stack: no dotfile, no per-person file, nothing read out of a home
directory.

EVERY READ BELOW SPELLS ITS KEY AS A LITERAL, ON PURPOSE. ``gate:config-contract``'s undeclared-read
scan is four regexes over the source, so a tidy ``get(name)`` wrapper would hide every key from it
and leave the declaration green over nothing. The declaration is ``core/mcp/config.v1.json`` and
that scan is what keeps it honest — it is sharp enough that it caught a key named in this very
paragraph, which is the correct amount of sharp.
"""
from __future__ import annotations

import os
import pathlib

# ── the siblings ─────────────────────────────────────────────────────────────────────────────
# VEXA_URL is the stack's gateway — the one URL a deployment must name; the rest default to the
# in-network service names, which is what a compose or helm deployment actually has.
URL = (os.environ.get("VEXA_URL") or "http://gateway:8000").rstrip("/")
GATEWAY = (os.environ.get("VEXA_GATEWAY_URL") or URL).rstrip("/")
AGENT_API = (os.environ.get("VEXA_AGENT_API_URL") or "http://agent-api:8100").rstrip("/")
ADMIN_API = (os.environ.get("VEXA_ADMIN_API_URL") or "http://admin-api:8001").rstrip("/")
FLOWS_API = (os.environ.get("VEXA_FLOWS_API_URL") or "http://flows-api:8200").rstrip("/")
# The mail DOUBLE — a dev-lane inbox, never a service. Unset in a real deployment, which is what
# makes `mail_inbox`/`mail_read` answer "not configured here" instead of inventing an inbox.
MAILPIT = (os.environ.get("MAILPIT_URL") or "").rstrip("/")
UI_BASE = (os.environ.get("VEXA_UI_URL") or "").rstrip("/")
CANONICAL = os.environ.get("VEXA_PUBLIC_MCP_URL") or "http://localhost:18310/mcp"

# ── credentials ──────────────────────────────────────────────────────────────────────────────
# The platform admin token, AS A DEPLOYMENT VALUE. The rig lifted this out of another container's
# environment with `docker inspect vexa-dogfood-admin-api-1` — a docker socket and a hardcoded
# container name, for a string (seam inventory B6.2). It is a credential; it arrives the way
# credentials arrive.
ADMIN_API_TOKEN = os.environ.get("VEXA_ADMIN_API_TOKEN") or ""
FLOWS_API_KEY = os.environ.get("VEXA_FLOWS_API_KEY") or ""
DELEGATION_SECRET = os.environ.get("VEXA_MCP_DELEGATION_SECRET") or ""
# The same internal secret the rest of the stack's internal tier checks (#526), under the same name,
# from the same contract. A server-to-server caller presents it instead of a person's token.
INTERNAL_API_SECRET = os.environ.get("VEXA_INTERNAL_API_SECRET") or ""

# ── behaviour ────────────────────────────────────────────────────────────────────────────────
# The default regime for a workspace nothing has been recorded about. `cloud` = the files live on
# the stack and the workspace verbs read and write them there. `local` = the files live on the
# person's own machine and NO cloud agent runs for them: the workspace verbs still operate on the
# cloud copy, git (workspace_pull / workspace_push) is the sync, and the person's own agent writes
# the local files itself with its native tools.
WORKSPACE_REGIME = (os.environ.get("VEXA_WORKSPACE_REGIME") or "cloud").strip().lower()
# The `token=` call-argument fallback and the `GET /do` bridge put a credential in a query string.
# Right for a fetch-only agent on a private host, wrong anywhere requests are logged — so unlike the
# rig, which defaulted it ON, the shipped default is OFF and the dogfood lane opts in.
RIG_MODE = (os.environ.get("VEXA_RIG_MODE") or "0") != "0"
MAIL_SMTP_HOST = os.environ.get("VEXA_MAIL_SMTP_HOST") or "localhost"
MAIL_SMTP_PORT = int(os.environ.get("VEXA_MAIL_SMTP_PORT") or "1025")
MAIL_ADDR = os.environ.get("VEXA_MAIL_ADDR") or ""
DOCS_BASE = (os.environ.get("VEXA_DOCS_URL") or "https://docs.vexa.ai").rstrip("/")
PORT = int(os.environ.get("PORT") or "18310")

# ── the state this edge keeps, all of it under one directory ─────────────────────────────────
# NOT config: a volume. Durable `vxa_mcp_…` tokens, pending sign-in codes, the one gateway key per
# person this process minted. `tests/test_thin_forward.py` asserts no tool writes anywhere else.
VEXA_HOME = pathlib.Path(os.environ.get("VEXA_HOME") or (pathlib.Path.home() / ".vexa"))
TOKENS_FILE = VEXA_HOME / "mcp-tokens.json"
USER_KEYS_FILE = VEXA_HOME / "user-api-keys.json"
EMAIL_CODES = VEXA_HOME / "oauth" / "email-codes.json"
LOGINS = VEXA_HOME / "oauth" / "logins.json"
REGIMES = VEXA_HOME / "oauth" / "regimes.json"
REVOKED_FILE = VEXA_HOME / "mcp-delegation-revoked.json"
FRICTION_LOG = VEXA_HOME / "friction.jsonl"
CAPS_DIR = VEXA_HOME / "caps"

LOGIN_TTL = 900
