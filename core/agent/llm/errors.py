"""errors.py — provider-neutral failure taxonomy + the auth fail-loud machinery (WS1).

A 401 from a model provider (a key sent to the wrong endpoint) used to surface as a generic
"model inference failed" — the operator was never told the token and the endpoint disagreed. The
signature detector, the provider-host resolver, the boot preflight, and the two UnitEvent builders
live HERE so every adapter and every consumer shares one vocabulary. Event shapes are FROZEN — the
terminal reducer and the SSE relay consume them field-for-field.
"""
from __future__ import annotations

import os
import re


class LLMError(Exception):
    """A completion/harness call failed for a non-auth reason (HTTP 5xx, bad payload, transport)."""


class LLMConfigError(LLMError):
    """The provider is not configured (missing base URL / model / unknown registry key) — fail loud
    with the exact env var to set, never limp into a confusing downstream error."""


class LLMAuthError(LLMError):
    """The provider rejected the credential (401/403) — surfaced distinctly so the operator sees the
    token/endpoint mismatch instead of a generic model error."""


# Substrings that mark a provider authentication failure in provider/CLI output.
_AUTH_SIGNATURE_RE = re.compile(
    r"\b401\b"
    r"|unauthorized"
    r"|invalid[ _-]*bearer"
    r"|invalid[ _-]*(?:x-)?api[ _-]*key"
    r"|authentication[ _-]*error"
    r"|no auth credentials"
    r"|user not found",  # OpenRouter's 401 body for a bad key
    re.IGNORECASE,
)


def looks_like_auth_failure(text: object) -> bool:
    """True if a blob of provider/CLI output carries an authentication-failure signature (401/
    Unauthorized/invalid bearer/invalid api key/authentication_error). Used to upgrade a generic
    model error into a distinct, actionable auth-error."""
    if not text:
        return False
    return bool(_AUTH_SIGNATURE_RE.search(str(text)))


def provider_host(base_url: str | None = None) -> str:
    """The host the credential is being sent to, for the auth-error hint. Resolution order:
    explicit arg → ``VEXA_LLM_BASE_URL`` (the completion provider) → ``ANTHROPIC_BASE_URL`` (the
    claude-code harness) → ``"unknown"``."""
    raw = base_url if base_url is not None else (
        os.environ.get("VEXA_LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL", "")
    )
    raw = (raw or "").strip()
    if not raw:
        return "unknown"
    from urllib.parse import urlparse

    host = urlparse(raw).netloc or urlparse(raw).path  # bare host (no scheme) lands in .path
    return host.strip("/") or raw


def auth_error_event(detail: object, *, model: str | None, stage: str) -> dict:
    """A DISTINCT auth-error event (NOT the generic model-error): names the provider host and tells
    the operator to reconcile the endpoint with the credential. Shape is frozen:
    ``{"type":"auth-error","error":{stage,model,provider_host,hint,message}}``."""
    host = provider_host()
    text = " ".join(str(detail or "provider rejected the token").split())
    return {
        "type": "auth-error",
        "error": {
            "stage": stage,
            "model": model or "",
            "provider_host": host,
            "hint": (
                f"provider {host} returned an auth failure (401) — token/endpoint mismatch; "
                "check VEXA_LLM_BASE_URL vs VEXA_LLM_API_KEY (completions) or ANTHROPIC_BASE_URL "
                "vs ANTHROPIC_AUTH_TOKEN (the claude-code runner) — an sk-or- token must go to "
                "openrouter.ai, an sk-ant- token to api.anthropic.com"
            ),
            "message": text[:600],
        },
    }


def model_error_event(message: object, *, model: str | None, stage: str) -> dict:
    """The generic model-failure event. Shape is frozen:
    ``{"type":"model-error","error":{stage,model,message}}``."""
    text = " ".join(str(message or "model inference failed").split())
    return {"type": "model-error", "error": {"stage": stage, "model": model or "", "message": text[:600]}}


def _mismatch(tok: str, host: str, *, key_var: str, url_var: str) -> str | None:
    """The known-bad prefix/host combinations. Conservative by design: only fires on a pair that
    WILL 401, never nags on a legitimate custom gateway."""
    is_openrouter_host = "openrouter.ai" in host
    is_anthropic_host = "api.anthropic.com" in host
    if tok.startswith("sk-or-") and is_anthropic_host:
        return (
            f"PROVIDER MISMATCH: {key_var} looks like an OpenRouter key (sk-or-…) but "
            f"{url_var} points at {host} — this will 401. Point the base_url at openrouter.ai/api "
            "or supply an Anthropic (sk-ant-…) token."
        )
    if tok.startswith("sk-ant-") and is_openrouter_host:
        return (
            f"PROVIDER MISMATCH: {key_var} looks like an Anthropic key (sk-ant-…) but "
            f"{url_var} points at {host} — this will 401. Point the base_url at api.anthropic.com "
            "or supply an OpenRouter (sk-or-…) token."
        )
    return None


def preflight_provider_guard(*, base_url: str | None = None, token: str | None = None) -> str | None:
    """Cheap boot guard: if a credential PREFIX and its base-url HOST obviously disagree, return a
    loud warning string (the caller logs it). Judges the completion pair (``VEXA_LLM_API_KEY`` /
    ``VEXA_LLM_BASE_URL``) first, then the claude-code pair (``ANTHROPIC_AUTH_TOKEN`` /
    ``ANTHROPIC_BASE_URL``). Explicit kwargs judge just that one pair (back-compat with callers
    that pass them). Returns None when consistent or unjudgeable."""
    if base_url is not None or token is not None:
        tok = (token if token is not None else os.environ.get("ANTHROPIC_AUTH_TOKEN", "")) or ""
        url = base_url if base_url is not None else os.environ.get("ANTHROPIC_BASE_URL", "")
        return _mismatch(tok.strip(), provider_host(url).lower(),
                         key_var="ANTHROPIC_AUTH_TOKEN", url_var="ANTHROPIC_BASE_URL")
    llm_key = (os.environ.get("VEXA_LLM_API_KEY") or "").strip()
    llm_url = (os.environ.get("VEXA_LLM_BASE_URL") or "").strip()
    if llm_key and llm_url:
        warn = _mismatch(llm_key, provider_host(llm_url).lower(),
                         key_var="VEXA_LLM_API_KEY", url_var="VEXA_LLM_BASE_URL")
        if warn:
            return warn
    tok = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    ant_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    return _mismatch(tok, provider_host(ant_url).lower(),
                     key_var="ANTHROPIC_AUTH_TOKEN", url_var="ANTHROPIC_BASE_URL")
