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

# The compose-mounted subscription credential file (same :ro mount the runtime probes; the
# docker backend mounts the same host path into workers at /root/.claude/.credentials.json).
CREDS_PATH = "/var/lib/vexa/host-claude-credentials"

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


def _post(url: str, payload: dict, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _get(url: str, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _result(ok: bool, summary: str, **extra) -> dict:
    return {"ok": ok, "summary": summary, **extra}


# ── models ────────────────────────────────────────────────────────────────────────────────────

def test_subscription_credentials(creds_path: str = CREDS_PATH, *, now: Optional[float] = None) -> dict:
    """The mounted credentials file: present → parseable → unexpired. Expiry IS the recurring
    local failure (stale Keychain export), so the failure message ships the exact remedy."""
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


def parse_extra_headers(raw: object) -> dict:
    """The configured extra request headers (#1667), ``Name: Value`` one per line — the format
    admin-api normalizes to on write and the format the claude CLI's ``ANTHROPIC_CUSTOM_HEADERS``
    parses. Local and deliberately tiny: the control-plane image does not ship ``llm/`` (D1), so
    the probe reads the same STRING the dispatch overlay passes through rather than importing the
    worker's parser. Unparseable lines are skipped — a bad header never breaks the test."""
    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items()
                if str(k).strip() and str(v).strip()}
    out: dict = {}
    for line in str(raw or "").replace("\r\n", "\n").split("\n"):
        name, sep, value = line.partition(":")
        if sep and name.strip() and value.strip():
            out[name.strip()] = value.strip()
    return out


# VEXA_LLM_PROVIDER → the dialect that provider's completion call speaks. None = it makes no HTTP
# completion call (claude-cli rides the subscription CLI), so there is nothing to probe.
_COMPLETION_DIALECT = {"openai-compat": "openai", "anthropic": "anthropic", "claude-cli": None}


def _grade_body(dialect: str, base: str, path: str, body: str) -> Optional[str]:
    """What is WRONG with a 2xx body, or None if it is the dialect's own success shape.

    The whole point of Vexa-ai/vexa#1666: a gateway that answers HTTP 200 with the *other*
    dialect's body (or with a CDN's HTML page) used to test GREEN here while every turn failed —
    the probe graded the status code and never looked at what came back. It looks now."""
    text = (body or "").strip()
    if not text:
        return f"HTTP 200 with an EMPTY body at {path} — a proxy or gateway is intercepting."
    if text[:1] == "<":
        return (f"an HTML page, not an API response, at {path} — a CDN or gateway (e.g. "
                f"Cloudflare) in front of {base} intercepted the request.")
    try:
        payload = json.loads(text)
    except ValueError:
        return f"a non-JSON body at {path}: {text[:160]}"
    if not isinstance(payload, dict):
        return f"a non-object JSON body at {path}: {text[:160]}"
    if dialect == "anthropic":
        if isinstance(payload.get("content"), list):
            return None
        if isinstance(payload.get("choices"), list):
            return (f"an OpenAI chat.completion body at {path} — this endpoint speaks the OPENAI "
                    f"dialect there, and the claude-code harness (which posts {path}) rejects "
                    f"that as 'empty or malformed'.")
    else:
        if isinstance(payload.get("choices"), list):
            return None
        if isinstance(payload.get("content"), list):
            return (f"an Anthropic Messages body at {path} — this endpoint speaks the ANTHROPIC "
                    f"dialect there, and the completion adapters cannot read it.")
    return f"an unrecognized 2xx body at {path}: {text[:160]}"


def _probe_dialect(base: str, path: str, dialect: str, api_key: str, model: str,
                   extra: dict, post: HttpPost) -> dict:
    """One dialect, ONE request, with ONLY that dialect's auth header.

    Sending ``x-api-key`` and ``Authorization: Bearer`` together (what this probe used to do) makes
    a gateway with per-dialect auth untestable: whichever header it wants is always present, so it
    answers 200 here and 401 in production. Each shape is now asked its own question — and a 401 is
    TERMINAL for that shape (never retried with the other header: the credential is wrong for this
    endpoint, and re-asking is the ~70s retry loop of #1666 in miniature)."""
    payload = {"model": model, "max_tokens": 1,
               "messages": [{"role": "user", "content": "ping"}]}
    headers = dict(extra)
    if dialect == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        status, body = post(f"{base}{path}", payload, headers)
    except Exception as exc:  # DNS, refused, TLS, timeout — the endpoint itself is the problem
        return {"ok": False, "status": None, "detail": f"unreachable: {exc}"}
    if status in (401, 403):
        want = "x-api-key" if dialect == "anthropic" else "Authorization: Bearer"
        return {"ok": False, "status": status,
                "detail": f"HTTP {status} — the key sent as '{want}' was rejected at {path}."}
    if status in (404, 405):
        return {"ok": False, "status": status,
                "detail": f"HTTP {status} — no {dialect} endpoint at {path}."}
    if not 200 <= status < 300:
        return {"ok": False, "status": status,
                "detail": f"HTTP {status} at {path}: {(body or '')[:160]}"}
    wrong = _grade_body(dialect, base, path, body)
    if wrong:
        return {"ok": False, "status": status, "detail": f"HTTP {status} answered {wrong}"}
    return {"ok": True, "status": status, "detail": f"HTTP {status}, {dialect} body OK"}


def test_custom_endpoint(base_url: str, api_key: str, model: str = "",
                         post: HttpPost = _post, *, harness_base_url: str = "",
                         harness_api_key: str = "", headers: object = "",
                         completion_dialect: Optional[str] = "openai") -> dict:
    """A REAL 1-token completion against EACH call shape the dispatch overlay brokers, with that
    shape's own endpoint, auth header and success body:

    * ``{harness_base_url or base_url}/v1/messages`` + ``x-api-key`` — what agent chat rides.
    * ``{base_url}/chat/completions`` + ``Authorization: Bearer`` — what meeting beats ride when
      the completion provider is ``openai-compat`` (the default, and what ``mode: custom`` always
      stamps). ``completion_dialect="anthropic"`` moves that probe to ``/v1/messages``; ``None``
      (the ``claude-cli`` provider) drops it — those beats ride the CLI, not HTTP.

    Every probed shape must pass, because every probed shape runs. The harness is reported first:
    a deployment whose Messages side fails has no working chat, whatever the other side says."""
    base = (base_url or "").rstrip("/")
    h_base = (harness_base_url or "").rstrip("/") or base
    if not (base or h_base):
        return _result(False, "Custom mode but no Base URL set.")
    model = model or "claude-haiku-4-5-20251001"
    extra = parse_extra_headers(headers)
    harness = _probe_dialect(h_base, "/v1/messages", "anthropic",
                             harness_api_key or api_key, model, extra, post)
    c_path = "/v1/messages" if completion_dialect == "anthropic" else "/chat/completions"
    c_label = ("Anthropic Messages" if completion_dialect == "anthropic"
               else "OpenAI chat-completions")
    # Skip the second probe when it would repeat the first (same dialect, same endpoint) or when
    # the beats make no HTTP completion call at all.
    same_call = completion_dialect == "anthropic" and base == h_base
    completion = (_probe_dialect(base, c_path, completion_dialect, api_key, model, extra, post)
                  if base and completion_dialect and not same_call else None)
    shapes = {"harness": {"endpoint": f"{h_base}/v1/messages", **harness}}
    if completion is not None:
        shapes["completion"] = {"endpoint": f"{base}{c_path}", **completion}
    extras_note = (f" Extra headers sent: {', '.join(sorted(extra))}." if extra else "")
    if harness["ok"] and (completion is None or completion["ok"]):
        where = "both call shapes" if completion is not None else "the agent call shape"
        return _result(True, f"Live completion OK on {where} (model {model}).{extras_note}",
                       status=harness.get("status"), shapes=shapes)
    broken = []
    if not harness["ok"]:
        broken.append(f"agent chat (Anthropic Messages at {h_base}/v1/messages): {harness['detail']}")
    if completion is not None and not completion["ok"]:
        broken.append(f"meeting beats ({c_label} at {base}{c_path}): {completion['detail']}")
    return _result(False, " · ".join(broken) + extras_note,
                   status=harness.get("status"), shapes=shapes)


def run_models_test(config: dict, env: Optional[dict] = None,
                    creds_path: str = CREDS_PATH, post: HttpPost = _post) -> dict:
    """The EFFECTIVE model credential test — same resolution the dispatch overlay applies
    (Settings user > global config already collapsed by admin-api; env is the floor)."""
    env = env if env is not None else dict(os.environ)
    mode = (config.get("mode") or "").strip()
    base_url = (config.get("base_url") or "").strip() or env.get("VEXA_LLM_BASE_URL", "") \
        or env.get("ANTHROPIC_BASE_URL", "")
    api_key = (config.get("api_key") or "").strip() or env.get("ANTHROPIC_AUTH_TOKEN", "") \
        or env.get("ANTHROPIC_API_KEY", "")
    harness_base_url = (config.get("harness_base_url") or "").strip() \
        or env.get("ANTHROPIC_BASE_URL", "")
    harness_api_key = (config.get("harness_api_key") or "").strip()
    headers = config.get("headers") or env.get("VEXA_LLM_EXTRA_HEADERS", "")
    # WHICH dialect the beats speak is the completion provider's business, not an assumption:
    # `mode: custom` always stamps openai-compat, but an env-configured deployment may run the
    # `anthropic` adapter (the same Messages dialect) or `claude-cli` (no HTTP completion call at
    # all). Probing /chat/completions regardless would fail a deployment that works.
    provider = (config.get("provider") or env.get("VEXA_LLM_PROVIDER") or "").strip()
    completion_dialect = _COMPLETION_DIALECT.get(provider or "openai-compat", "openai")
    if mode == "custom":
        completion_dialect = "openai"  # what overlay_model_config stamps for this mode
    if mode == "custom" or (not mode and base_url and api_key):
        out = test_custom_endpoint(base_url, api_key, (config.get("model") or "").strip(),
                                   post=post, harness_base_url=harness_base_url,
                                   harness_api_key=harness_api_key, headers=headers,
                                   completion_dialect=completion_dialect)
        out["mode"] = "custom"
    else:
        out = test_subscription_credentials(creds_path)
        out["mode"] = "subscription"
    # Non-secret provenance so the UI can say WHAT was tested.
    out["config"] = {k: v for k, v in config.items()
                     if k in ("mode", "model", "meeting_model", "base_url",
                              "harness_base_url") and v}
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
