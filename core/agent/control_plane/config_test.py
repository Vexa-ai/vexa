"""config_test.py — on-demand credential tests behind Settings → Models "Test" buttons.

The two silent-failure modes this surface exists to catch (both bit the owner on 2026-07-09):
  * subscription mode: the mounted ``~/.claude/.credentials.json`` is a STALE export (macOS
    Keychain holds the live token; the file expires ~8-12h) → every turn dies with
    "401 Invalid authentication credentials" and nothing in the UI says why.
  * transcription: a Settings-level override silently outranks ``.env`` (user > global > env)
    and a zero-balance external token 402s every segment while the bot logs stay in docker.

Tests are HONEST about depth: a custom endpoint gets a real 1-token completion; the
subscription file gets an existence + expiry check (the CLI lives only in worker images, so a
live inference test would need a dispatch — the expiry check catches 100% of the observed
failures); the transcription backend gets a real authenticated ``/balance`` probe.

Pure functions over injected fetchers — the routes in api.py are thin wrappers.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from control_plane import model_endpoint
from shared.host_claude import LEGACY_CREDENTIALS_MOUNT, credentials_path

# The compose-mounted subscription credential (same mounts the runtime probes; the docker backend
# brokers the same host file into workers at /root/.claude/.credentials.json).
#
# NOT a resolved path — deliberately. This used to be
#     CREDS_PATH = "/var/lib/vexa/host-claude-credentials"
# bound straight into `def test_subscription_credentials(creds_path=CREDS_PATH)`, i.e. decided ONCE
# at import. Under the old single-FILE bind that also pinned an inode, so a token the claude CLI
# had already refreshed on the host stayed invisible until the container was recreated — 32h of
# "Not logged in" on the dogfood stack. `credentials_path()` re-resolves per call and prefers the
# DIRECTORY mount; see shared/host_claude.py for the mechanics.
CREDS_PATH = LEGACY_CREDENTIALS_MOUNT

# The macOS remedy, verbatim — the error message must carry the fix (fail loud AND helpful).
KEYCHAIN_REFRESH = ('security find-generic-password -s "Claude Code-credentials" -w '
                    "> ~/.claude/.credentials.json")

_TIMEOUT = 8.0
# The audio round-trip probe transcribes a real ~1s clip — give the model time to answer.
_STT_PROBE_TIMEOUT = 20.0

# (status, body_text) — injectable for tests; None body on network failure.
HttpPost = Callable[[str, dict, dict], tuple[int, str]]
HttpGet = Callable[[str, dict], tuple[int, str]]
# (endpoint, token) → (status, body_text) for the STT audio round-trip — injectable for tests.
TranscribeProbe = Callable[[str, str], tuple[int, str]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is the endpoint's answer, never followed: following it would carry the API key to
    whatever host the endpoint names, past the allow-list that admitted the endpoint."""

    def redirect_request(self, *a, **kw):
        return None


_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


def _post(url: str, payload: dict, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with _NO_REDIRECT.open(req, timeout=_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _subject_post(url: str, payload: dict, headers: dict) -> tuple[int, str]:
    """``_post`` for an endpoint a PERSON chose: no redirects, ever (a redirect would carry their
    key and our request somewhere else), and a host that only a wildcard admits is reached through
    the outbound URL guard's pinned transport, so it must resolve to public addresses. A host the
    operator allow-listed by its exact name is reached as named (a self-hosted model on a private
    network is that operator's choice)."""
    import httpx
    from urllib.parse import urlsplit

    from shared import ssrf

    host = (urlsplit(url).hostname or "").lower()
    transport = None if model_endpoint.named_exactly(host) else ssrf.build_pinned_sync_transport()
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False, transport=transport) as c:
        r = c.post(url, json=payload, headers=headers)
        return r.status_code, r.text


def _get(url: str, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _NO_REDIRECT.open(req, timeout=_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _result(ok: bool, summary: str, **extra) -> dict:
    return {"ok": ok, "summary": summary, **extra}


# ── models ────────────────────────────────────────────────────────────────────────────────────

def test_subscription_credentials(creds_path: Optional[str] = None, *, now: Optional[float] = None) -> dict:
    """The mounted credentials file: present → parseable → unexpired. Expiry IS the recurring
    local failure (stale Keychain export), so the failure message ships the exact remedy.

    ``creds_path=None`` resolves the mount FRESH on every call (directory mount first, legacy file
    mount second). A default argument would be evaluated once at import — which is precisely how
    this surface kept reporting a token that the host had already refreshed."""
    creds_path = creds_path or credentials_path()
    if not os.path.isfile(creds_path):
        # docker turns a MISSING host path into an empty dir — same failure, same message.
        return _result(False, "No subscription credentials mounted "
                              "(HOST_CLAUDE_CREDENTIALS unset, or the host file is missing) — "
                              "all setup options: https://docs.vexa.ai/configuration")
    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _result(False, "Credentials file is unreadable or not JSON — re-export it: "
                              + KEYCHAIN_REFRESH)
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    expires_ms = (oauth or data or {}).get("expiresAt") if isinstance(oauth or data, dict) else None
    if not isinstance(expires_ms, (int, float)):
        return _result(False, "Credentials file has no expiresAt — not a Claude Code "
                              "credential export? Re-export it: " + KEYCHAIN_REFRESH)
    left_h = (expires_ms / 1000.0 - (now if now is not None else time.time())) / 3600.0
    if left_h <= 0:
        return _result(False, "Subscription token EXPIRED (stale Keychain export — the known "
                              "macOS gotcha). Refresh with: " + KEYCHAIN_REFRESH,
                       expired=True)
    return _result(True, f"Subscription credentials valid — token expires in {left_h:.1f} h. "
                         "(File check; inference itself runs in workers.)",
                   expires_in_hours=round(left_h, 1))


def test_custom_endpoint(base_url: str, api_key: str, model: str = "",
                         post: HttpPost = _post, extra_body: str = "") -> dict:
    """A REAL 1-token completion against the configured endpoint. Anthropic-style first
    (``/v1/messages``), OpenAI-compat fallback (``/v1/chat/completions``) on 404/405 — the two
    dialects a custom endpoint may speak.

    ``extra_body`` is sent on the openai-compat attempt exactly as a real turn sends it. Testing
    WITHOUT it would be the worst kind of green: a self-hosted Qwen answers 200 to a ping in
    thinking mode and then returns no valid JSON in production, so a test that omitted the field
    would certify a configuration that cannot work."""
    base = base_url.rstrip("/")
    if not base:
        return _result(False, "Custom mode but no Base URL set.")
    # THE SAME GATE THE DISPATCH APPLIES (F84 · F93). A Test button that greens an endpoint the
    # dispatch will refuse is worse than no button: it tells the operator the configuration works
    # and the turn then silently runs on the deployment's own model. One predicate, both surfaces.
    refusal = model_endpoint.refuse_reason(base)
    if refusal:
        return _result(False, f"Refused before any request was made: {refusal}")
    # A base URL may or may not already carry the /v1 suffix — both spellings are common and the
    # completion adapter accepts either (it posts {base}/chat/completions). Appending a second /v1
    # produced /v1/v1/... against a real vLLM: a 404 that reads as "endpoint broken" when the
    # configuration was fine. Normalise once, here.
    root = base[:-3].rstrip("/") if base.endswith("/v1") else base
    if not model:
        # MODEL-AGNOSTIC: no vendor default. Guessing a model name is a coin-flip that fails as a
        # confusing 400/404 from someone else's endpoint ("model not found: claude-…" from a vLLM
        # serving Qwen). Say what is missing instead.
        return _result(False, "Custom mode but no model set — name the model this endpoint serves.")
    auth = {"x-api-key": api_key, "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01"}
    try:
        status, body = post(f"{root}/v1/messages",
                            {"model": model, "max_tokens": 1,
                             "messages": [{"role": "user", "content": "ping"}]}, auth)
        if status in (404, 405):  # not an anthropic dialect — try openai-compat
            oai: dict = {"model": model, "max_tokens": 1,
                         "messages": [{"role": "user", "content": "ping"}]}
            if extra_body.strip():
                try:
                    parsed = json.loads(extra_body)
                except ValueError as exc:
                    return _result(False, f"Extra body is not valid JSON: {exc}")
                if not isinstance(parsed, dict):
                    return _result(False, "Extra body must be a JSON object.")
                oai = {**parsed, **oai}  # reserved keys always win, as in the adapter
            status, body = post(f"{root}/v1/chat/completions", oai, auth)
    except Exception as exc:  # DNS, refused, TLS, timeout — the endpoint itself is the problem
        return _result(False, f"Endpoint unreachable: {exc}")
    if status in (401, 403):
        return _result(False, f"Authentication FAILED at {base} (HTTP {status}) — bad or "
                              "expired API key.", status=status)
    if 200 <= status < 300:
        return _result(True, f"Live completion OK against {base} (model {model}).",
                       status=status)
    detail = body[:200] if body else ""
    return _result(False, f"Endpoint answered HTTP {status}: {detail}", status=status)


def run_models_test(config: dict, env: Optional[dict] = None,
                    creds_path: Optional[str] = None, post: Optional[HttpPost] = None) -> dict:
    """The EFFECTIVE model credential test: the route this subject's turn would take, as the
    dispatch decides it. The decision is not restated here: ``overlay_model_config`` (the operator
    gate, ``subject_route_env``, the model allowlist) runs on an empty env and the probe reads what
    it stamped (F84 · F93 · Vexa-ai/vexa#1783).

    There are two routes, and a probe never mixes them:

    * **The subject's own**, when a custom endpoint passes the gate. Probed with the subject's key
      (empty included), model and extra_body: the values the worker receives.
    * **The deployment's**, in every other case. Probed with the deployment's credential only: its
      gateway (``ANTHROPIC_BASE_URL`` with ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_API_KEY``) when
      one is configured, else the mounted subscription file. A key the subject stored is not used
      on this route, so it is sent nowhere, and the summary says that nothing of theirs was tested.

    A custom endpoint the gate refuses is reported as refused, and no request is made. The
    subject's own endpoint is probed through ``_subject_post`` (no redirects; a wildcard-admitted
    host only at public addresses); ``post``, when given, replaces both probes (tests)."""
    from control_plane.dispatch import overlay_model_config   # the dispatch's decision, reused

    env = env if env is not None else dict(os.environ)
    cfg = config if isinstance(config, dict) else {}
    route: dict[str, str] = {}
    overlay_model_config(route, cfg, allowlist=env.get("VEXA_MODEL_ALLOWLIST", ""))
    # The model the turn runs on: the subject's, when the allowlist kept it, else the deployment's.
    model = route.get("VEXA_AGENT_MODEL") or (env.get("VEXA_AGENT_MODEL") or "").strip()
    cfg_url = model_endpoint.custom_base_url(cfg)
    if cfg_url and route.get("VEXA_LLM_BASE_URL") == cfg_url:
        out = test_custom_endpoint(route["VEXA_LLM_BASE_URL"], route["VEXA_LLM_API_KEY"], model,
                                   post=post or _subject_post, extra_body=route["VEXA_LLM_EXTRA_BODY"])
        out["mode"], out["route"] = "custom", "subject"
    elif cfg_url:
        runner = route.get("VEXA_RUNNER") or (env.get("VEXA_RUNNER") or "").strip() or "claude-code"
        reason = (model_endpoint.route_refusal(cfg_url, str(cfg.get("api_key") or ""), runner)
                  or "the endpoint is not admitted")
        out = _result(False, f"Refused before any request was made: {reason}")
        out["mode"], out["route"] = "custom", "subject"
    else:
        out = _test_deployment_route(env, model, creds_path, post or _post)
        out["route"] = "deployment"
        runner = route.get("VEXA_RUNNER") or (env.get("VEXA_RUNNER") or "").strip()
        if runner == "openai-agent":
            # The worker reads that lane's own keys, and this image does not ship the harness that
            # orders them, so the button says what it did not test rather than guess.
            out["summary"] += (" Your turns run on openai-agent, whose deployment endpoint "
                               "(VEXA_LLM_BASE_URL) this button does not probe.")
        if (cfg.get("api_key") or "").strip() or (cfg.get("base_url") or "").strip():
            out["summary"] += (" Your stored endpoint settings are not in effect (mode is not "
                               "custom with a Base URL), so your turns run on the deployment's "
                               "credentials: nothing of yours was sent or tested.")
    # Non-secret provenance so the UI can say WHAT was tested.
    out["config"] = {k: v for k, v in cfg.items() if k in ("mode", "model", "base_url") and v}
    return out


def run_route_test(route, creds_path: Optional[str] = None,
                   post: Optional[HttpPost] = None, *, admin: bool = True) -> dict:
    """The Test button for one model of the catalog: the route the dispatch would stamp for it,
    resolved by the same provider port, probed in that route's own dialect with exactly that route's
    credential — the same URL the harness posts to, so a green here is the request a turn makes.

    * subscription — the mounted credential file (its expiry), as the deployment route is tested;
    * openai-agent — a 1-token ``POST {base_url}/chat/completions`` with the route's extra body;
    * claude-code — a 1-token ``POST {base_url}/v1/messages``, the key under the header the
      provider expects (bearer for a gateway, ``x-api-key`` for Anthropic's own API).

    A ``custom`` route is the person's own endpoint and keeps its own probe (both dialects).

    WHO SEES THE ENDPOINT'S OWN WORDS (``admin``). An operator's endpoint answers a failed probe
    with its own body and the Test button used to show up to 200 characters of it — and the
    endpoint's address — to any signed-in person, while the catalog listing withholds both. For
    anyone but an instance admin, an operator route's failure is the typed ``model-provider`` fault
    with no body and no address; the person's own endpoint (``custom``) is theirs and keeps its
    detail."""
    from control_plane import model_providers as mp

    post = post or _post          # resolved per call, never bound once at import
    model = route.provider_model
    operator = route.credential_source in (mp.CRED_SECRET, mp.CRED_NONE)   # an endpoint of the operator's
    if operator and not admin:
        return _quiet_route_test(route, creds_path, post)
    if route.credential_source == mp.CRED_SUBSCRIPTION:
        out = test_subscription_credentials(creds_path)
    elif route.credential_source == mp.CRED_SUBJECT:
        out = test_custom_endpoint(route.base_url, route.credential, model, post=post,
                                   extra_body=route.extra_body)
    elif not model:
        out = _result(False, "This model names no model id at its provider.")
    elif route.harness == "openai-agent":
        body: dict = {"model": model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "ping"}]}
        if route.extra_body:
            body = {**json.loads(route.extra_body), **body}   # reserved keys win, as in the harness
        headers = {"Authorization": f"Bearer {route.credential}"} if route.credential else {}
        out = _probe(post, f"{route.base_url.rstrip('/')}/chat/completions", body, headers,
                     route.base_url, model)
    else:
        headers = {"anthropic-version": "2023-06-01"}
        if route.auth_header == "x-api-key":
            headers["x-api-key"] = route.credential
        else:
            headers["Authorization"] = f"Bearer {route.credential}"
        base = (route.base_url or "https://api.anthropic.com").rstrip("/")
        out = _probe(post, f"{base}/v1/messages",
                     {"model": model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "ping"}]},
                     headers, base, model)
    out["summary"] = f"{route.model_id} via {route.provider}: {out['summary']}"
    out.update(mode="catalog", route="catalog", model=route.model_id, provider=route.provider,
               harness=route.harness)
    return out


def _status_kind(status: Optional[int]) -> str:
    """The provider-call kind a probe's HTTP status means (unit.v1 ``ModelProviderFaultKind``)."""
    from shared.fault_wire import ModelProvider as V
    if status is None:
        return V.UNAVAILABLE
    if status == 402:
        return V.UNPAID
    if status in (401, 403):
        return V.UNAUTHORIZED
    if status == 429:
        return V.RATE_LIMITED
    if status >= 500 or status == 408:
        return V.UNAVAILABLE
    return V.REFUSED


def _quiet_route_test(route, creds_path: Optional[str], post: HttpPost) -> dict:
    """An operator route's Test, for a person who is not an instance admin: the same probe, the
    verdict and a typed fault — never the endpoint's body or address."""
    from shared.fault_wire import ModelProvider as V
    admin_view = run_route_test(route, creds_path, post, admin=True)
    out = {"mode": "catalog", "route": "catalog", "model": route.model_id,
           "provider": route.provider, "harness": route.harness}
    if admin_view.get("ok"):
        return {"ok": True, "summary": f"{route.model_id} via {route.provider}: the model answered.",
                **out}
    status = admin_view.get("status") if isinstance(admin_view.get("status"), int) else None
    fault = {"source": V.SOURCE, "kind": _status_kind(status), "provider": route.provider,
             "model": route.model_id, "status": status,
             "detail": (f"The model's provider answered HTTP {status}." if status
                        else "The model's provider could not be reached."),
             "remedy": "Ask an operator to check this model's provider, or pick another model."}
    return {"ok": False, "summary": f"{route.model_id} via {route.provider}: {fault['detail']}",
            "fault": fault, **({"status": status} if status else {}), **out}


def _probe(post: HttpPost, url: str, body: dict, headers: dict, base: str, model: str) -> dict:
    try:
        status, text = post(url, body, headers)
    except Exception as exc:  # DNS, refused, TLS, timeout — the endpoint itself is the problem
        return _result(False, f"Endpoint unreachable: {exc}")
    if status in (401, 403):
        return _result(False, f"Authentication FAILED at {base} (HTTP {status}) — the provider "
                              "rejected this model's credential.", status=status)
    if 200 <= status < 300:
        return _result(True, f"Live completion OK against {base} (model {model}).", status=status)
    return _result(False, f"Endpoint answered HTTP {status}: {(text or '')[:200]}", status=status)


def _test_deployment_route(env: dict, model: str, creds_path: Optional[str],
                           post: HttpPost) -> dict:
    """The deployment's own route, with the deployment's own credential and no extra body (the
    claude CLI sends none): its gateway when one is configured with a key, else the subscription
    file the worker mounts."""
    base = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    key = (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or "").strip()
    if base and key:
        out = test_custom_endpoint(base, key, model, post=post)
        out["summary"] = "Deployment gateway: " + out["summary"]
        out["mode"] = "custom"
    else:
        out = test_subscription_credentials(creds_path)
        out["mode"] = "subscription"
    return out


# ── transcription ─────────────────────────────────────────────────────────────────────────────

# The OpenAI-compatible transcriptions path every consumer agrees on. Appended only when the
# configured URL does not already carry it — the one rule shared with the config.v1 probe
# (deploy/contracts/config.v1/preflight.py:probe_url), the bot's client, and the dictation route.
_STT_PATH = "/v1/audio/transcriptions"

def _transcribe_probe(endpoint: str, token: str) -> tuple:
    """POST the shared audio probe body — the same request the boot preflight makes."""
    from control_plane.config_preflight import audio_probe_body

    content_type, body = audio_probe_body()
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": content_type, "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=_STT_PROBE_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _verify_transcribes(base: str, token: str, source: str, probe: TranscribeProbe,
                        account: str = "") -> dict:
    """Grade the backend by the ONE question the operator is actually asking: will a bot get a
    transcript out of this? Answered by sending real audio — the same request a bot's first chunk
    makes, and the same body the boot preflight sends.

    An EMPTY body cannot answer it: a metered backend answers an empty POST the same way whether
    the credential is funded or worthless, which is how a token that 402s every segment of every
    meeting used to test green here. Nor can the account's balance answer it — a billing-exempt
    account reports 0.0 minutes and transcribes perfectly, so a balance threshold condemns the
    working credential and clears nothing. Sending audio makes the verdict independent of whose
    token it is, so no account identity is named anywhere in this codebase."""
    endpoint = base if base.endswith(_STT_PATH) else base + _STT_PATH
    who = f" ({account})" if account else ""
    try:
        status, body = probe(endpoint, token)
    except Exception as exc:
        return _result(False, f"Backend unreachable: {exc}", source=source)
    if status in (401, 403):
        return _result(False, f"Token REJECTED by {endpoint} (HTTP {status}).", source=source,
                       status=status)
    if status == 402:
        detail = (body or "").strip()[:160]
        return _result(False, f"Token is valid{who} but cannot pay for transcription (HTTP 402) — "
                              f"every segment will fail and meetings will complete with NO "
                              f"transcript. Top up the account or use a token that can transcribe."
                              + (f" Backend said: {detail}" if detail else ""),
                       source=source, status=status, account=account or None)
    if status == 404:
        return _result(False, f"No transcriptions endpoint at {endpoint} (HTTP 404) — check the "
                              "URL shape; some gateways also answer 404 for a rejected key.",
                       source=source, status=status)
    if status >= 500:
        return _result(False, f"Backend error at {endpoint} (HTTP {status}).", source=source,
                       status=status)
    return _result(True, f"OK — {endpoint} transcribed the probe clip{who} (HTTP {status}).",
                   source=source, status=status, account=account or None)


def _guarded_get(url: str, headers: dict) -> tuple[int, str]:
    """``_get`` for a CUSTOMER endpoint: through the outbound URL guard's pinned transport, no redirects."""
    import httpx
    from shared import ssrf

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False,
                      transport=ssrf.build_pinned_sync_transport()) as c:
        r = c.get(url, headers=headers)
        return r.status_code, r.text


def _guarded_probe(endpoint: str, token: str) -> tuple:
    """``_transcribe_probe`` for a CUSTOMER endpoint: the same body, through the pinned transport."""
    import httpx
    from control_plane.config_preflight import audio_probe_body
    from shared import ssrf

    content_type, body = audio_probe_body()
    with httpx.Client(timeout=_STT_PROBE_TIMEOUT, follow_redirects=False,
                      transport=ssrf.build_pinned_sync_transport()) as c:
        r = c.post(endpoint, content=body,
                   headers={"Content-Type": content_type, "Authorization": f"Bearer {token}"})
        return r.status_code, r.text


def transcription_route(configured: Optional[dict], env: dict) -> tuple[str, str, str, str]:
    """``(url, token, source, provider)`` — the backend a bot spawned now would use and the one
    credential it would carry, by bot_spawn's rule: a configured URL (Settings, resolved by
    admin-api's bot-context) brings its own token, empty meaning none — never the deployment's; with
    no configured URL the deployment's URL and token apply, and a configured token alone is not used
    (bot_spawn does not use it either). The Test button probes exactly this pair. ``env`` is the
    deployment's ``TRANSCRIPTION_SERVICE_URL`` / ``_TOKEN``, read by the caller."""
    cfg = configured if isinstance(configured, dict) else {}
    url = str(cfg.get("url") or "").strip()
    if url:
        return url, str(cfg.get("token") or ""), "settings", str(cfg.get("provider") or "")
    return (str(env.get("TRANSCRIPTION_SERVICE_URL") or "").strip(),
            str(env.get("TRANSCRIPTION_SERVICE_TOKEN") or "").strip(), "env", "")


def run_customer_transcription_test(url: str, token: str, source: str, get: HttpGet = _guarded_get,
                                    probe: TranscribeProbe = _guarded_probe,
                                    resolver=None) -> dict:
    """``run_transcription_test`` for an endpoint the PERSON configured (their Settings), not the
    deployment: the URL must pass the outbound URL guard before anything is sent, and every request
    goes through its pinned transport — the Test button never probes this deployment's own network
    on somebody's behalf."""
    from shared import ssrf

    try:
        ssrf.validate_url((url or "").strip(), resolver, what="transcription endpoint")
    except ssrf.SSRFError as exc:
        return _result(False, f"Transcription endpoint refused: {exc}.", source=source)
    try:
        return run_transcription_test(url, token, source, get=get, probe=probe)
    except ssrf.SSRFError as exc:   # the connect-time re-check refused
        return _result(False, f"Transcription endpoint refused: {exc}.", source=source)


def run_transcription_test(url: str, token: str, source: str, get: HttpGet = _get,
                           probe: TranscribeProbe = _transcribe_probe) -> dict:
    """A real round-trip test of the effective STT backend: transcribe a ~1s probe clip with the
    configured token — the same request (and the same probe body) a bot's first chunk and the boot
    preflight make, so the wizard can never green what the deployment refuses.

    The endpoint's own answer is the verdict. Neither of the two indirect oracles survives contact
    with reality: an empty-body POST is answered identically for a funded and a worthless
    credential, and ``balance_minutes`` reads 0.0 for a billing-exempt account that transcribes
    perfectly — the 2026-07-19 recurrence was exactly a zero-balance token probing green while a
    zero-balance *exempt* token did the actual work. Sending audio asks the real question and keeps
    every account identity out of this codebase. ``/balance`` is still consulted first, but only to
    NAME the account in the verdict (a courtesy, never the oracle)."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return _result(False, "No transcription backend configured at any level "
                              "(user, global, or deployment env).", source=source)
    # Bots post to {url}/v1/audio/transcriptions — strip the path for the account lookup.
    probe_base = base
    for suffix in ("/v1/audio/transcriptions", "/v1/audio", "/v1"):
        if probe_base.endswith(suffix):
            probe_base = probe_base[: -len(suffix)]
            break
    if not token:
        return _result(False, f"Backend {probe_base} configured but NO token set ({source}).",
                       source=source)
    account = ""
    try:
        status, body = get(f"{probe_base}/balance", {"X-API-Key": token})
        if status == 200:
            try:
                account = (json.loads(body) or {}).get("email") or ""
            except ValueError:
                account = ""
    except Exception:
        pass  # no /balance ⇒ not a Vexa gateway; the round-trip below is the oracle either way
    return _verify_transcribes(base, token, source, probe, account)
