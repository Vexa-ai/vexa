"""model_grant — the one model credential a worker holds: a per-turn token to the model proxy.

No worker, turn or model shell holds a model-provider key (H5/W1). agent-api resolves the turn's
model route — endpoint, credential, model — keeps it sealed where only the model proxy reads it
(``control_plane.model_proxy``), and hands the worker a token instead. The token is:

* **per turn** — agent-api mints one for each message it delivers to a unit (the cold spawn's
  entrypoint in :data:`TOKEN_ENV`, every later message on its signed ``unit:<id>:in`` entry as
  :data:`ENTRY_FIELD`), and the worker revokes it when the run that used it ends;
* **short-lived** — it expires with the turn's own ceiling, revoked or not;
* **scoped** — the proxy forwards only to the route agent-api resolved, only for the turn's models,
  and only up to the turn's request ceiling.

The harness CLIs are pointed at the proxy (their base-URL names carry :data:`URL_ENV` + ``/llm``) and
present the token where they would have presented a key. The model's shell can read that token, as
it could read anything its CLI holds: what it reads is a turn-bound handle that works only on the
internal proxy, only until the turn ends — never a key that works anywhere else.

This module is the worker's half: which token the running turn uses (a context variable, so a
background job on its own thread keeps the token of the turn that started it), and the revocation
when the last run holding a token lets go of it. Standard library only — both images import it.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import threading
import urllib.request
from typing import Iterator, Optional

log = logging.getLogger("shared.model_grant")

#: The worker env var carrying the cold spawn's token. Taken out of the environment at boot.
TOKEN_ENV = "VEXA_MODEL_PROXY_TOKEN"
#: Where workers reach the model proxy (``http://model-proxy:8102`` in compose). agent-api config.
URL_ENV = "VEXA_MODEL_PROXY_URL"
#: The proxy's path for model traffic: a harness's base URL is ``<URL_ENV>`` + this.
LLM_PATH = "/llm"
#: The proxy's path for the toolbelt: a harness's MCP attachment names ``<URL_ENV>`` + this.
MCP_PATH = "/mcp"
#: The proxy's path for revoking a token, presented as that token's bearer.
REVOKE_PATH = "/grant/revoke"
#: The field of a ``unit:<id>:in`` entry carrying that message's token.
ENTRY_FIELD = "model_grant"

#: Every name a harness or its CLI reads a MODEL-PROVIDER credential from. None of them carries a
#: provider key into a worker: the dispatch stamps each one empty, the runtime forwards none of them,
#: and ``llm.ports.harness_subprocess_env`` strips each one from what a harness subprocess inherits.
PROVIDER_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                           "OPENAI_API_KEY", "CODEX_API_KEY", "VEXA_LLM_API_KEY")


class _Held:
    """One token and the runs holding it."""

    __slots__ = ("token", "refs")

    def __init__(self, token: str) -> None:
        self.token = token
        self.refs = 0


_current: contextvars.ContextVar[Optional[_Held]] = contextvars.ContextVar("model_grant", default=None)
_lock = threading.Lock()
_boot_token = ""


def take_boot_token() -> str:
    """The cold spawn's token, removed from this process's environment (so no subprocess built from
    ``os.environ`` inherits it) and kept for the entrypoint's run."""
    global _boot_token
    _boot_token = (os.environ.pop(TOKEN_ENV, "") or "").strip()
    return _boot_token


def boot_token() -> str:
    return _boot_token


def current() -> str:
    """The token of the run on this thread, or ``""`` outside any run."""
    held = _current.get()
    return held.token if held is not None else ""


def proxy_base() -> str:
    """The base URL a harness sends model requests to: the proxy's model path, or ``""``."""
    url = (os.environ.get(URL_ENV) or "").strip().rstrip("/")
    return url + LLM_PATH if url else ""


def proxy_toolbelt() -> str:
    """The MCP endpoint a harness attaches: the proxy's toolbelt path, or ``""`` (no proxy)."""
    url = (os.environ.get(URL_ENV) or "").strip().rstrip("/")
    return url + MCP_PATH if url else ""


def _revoke(token: str) -> None:
    url = (os.environ.get(URL_ENV) or "").strip().rstrip("/")
    if not (url and token):
        return
    req = urllib.request.Request(url + REVOKE_PATH, data=b"", method="POST",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 — the token still expires on its own
        log.warning("model grant: revocation not confirmed (%s); it expires with its turn",
                    type(exc).__name__)


def discard(token: Optional[str]) -> None:
    """Revoke a token no run will use (a message that joined the turn already running)."""
    if token and str(token).strip():
        _revoke(str(token).strip())


def _release(held: _Held) -> None:
    with _lock:
        held.refs -= 1
        last = held.refs <= 0
    if last:
        _revoke(held.token)


@contextlib.contextmanager
def use(token: Optional[str]) -> Iterator[None]:
    """Run the body as one run holding ``token``. When the last run holding it ends, it is revoked:
    a turn's token stops working when its turn does, not only when it expires."""
    token = (token or "").strip()
    if not token:
        reset = _current.set(None)
        try:
            yield
        finally:
            _current.reset(reset)
        return
    held = _Held(token)
    with _lock:
        held.refs += 1
    reset = _current.set(held)
    try:
        yield
    finally:
        _current.reset(reset)
        _release(held)


def capture() -> Optional[_Held]:
    """A hold on this thread's token for a run that outlives the turn on another thread (a
    background job): the token is revoked only when that run lets go too. Pair with :func:`adopt`."""
    held = _current.get()
    if held is not None:
        with _lock:
            held.refs += 1
    return held


@contextlib.contextmanager
def adopt(held: Optional[_Held]) -> Iterator[None]:
    """Run the body on another thread under a hold :func:`capture` took, and let go of it after."""
    reset = _current.set(held)
    try:
        yield
    finally:
        _current.reset(reset)
        if held is not None:
            _release(held)
