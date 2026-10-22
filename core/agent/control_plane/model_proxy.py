"""model_proxy.py — the model-provider key proxy: no worker, turn or model shell holds a provider key.

A worker's harness drives a model-provider API: the claude CLI speaks Anthropic's ``/v1/messages``,
Codex speaks OpenAI's ``/responses``, our own openai-agent loop speaks ``/chat/completions``. Each
needs a credential, and whatever a harness CLI holds its model's shell can read. So no harness holds
one. agent-api resolves the turn's model route exactly as before (``control_plane.dispatch``: the
catalog, the person's own endpoint, the deployment's keys and subscription) and then :func:`confine`
takes that route OUT of the worker's environment:

* the route — endpoint, credential (or which subscription file), the turn's models and its output
  cap — is sealed (AES-GCM, :data:`SECRET_ENV`) and kept in Redis under ``mproxy:route:<id>``, a key
  no worker's Redis user may read;
* the worker is handed a per-turn token instead (``shared.model_grant``): ``mproxy:grant:<sha256>``
  names the unit, the route and the turn's request ceiling, and expires with the turn;
* every base-URL name the harnesses read points at this proxy, and every provider-credential name is
  stamped empty, so nothing of the deployment's own route can be refilled downstream;
* the toolbelt goes the same way: a harness's MCP attachment names this proxy's ``/mcp`` with the
  turn's token, and the proxy presents the unit's delegation bearer to the gateway — the bearer is
  never in a file or an environment the model's shell can read.

The proxy (:func:`create_app`, ``python -m control_plane.model_proxy``) is a separate process that
workers can reach and that holds no trust header of any kind: a request is authenticated by its
token alone. It refuses a token it does not hold (unknown, revoked, or expired), a model outside the
turn's scope, and a request past the turn's ceiling; otherwise it replaces the token with the route's
credential and forwards the request byte for byte, streaming the answer back as it arrives.

A deployment SUBSCRIPTION is the one exception, and deliberately so: a consumer OAuth credential
(the claude CLI's ``.credentials.json`` or ``CLAUDE_CODE_OAUTH_TOKEN``, Codex's ``auth.json``) is
read only by the vendor's own CLI from its own place, never extracted into a proxy's headers
(``docs/docs/model-credentials-licensing.mdx``, operator checklist). A subscription route therefore
keeps its credential with the CLI and its model calls go straight to the vendor; the licence
confines it to the one human it belongs to. Its toolbelt still goes through the proxy.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from shared import model_grant as mg
from shared import units

logger = logging.getLogger("agent_api.model_proxy")

#: The port the proxy listens on, on every deploy surface.
PORT = 8102
#: The key agent-api seals a route with and the proxy opens it with (``Settings.model_proxy_secret``).
SECRET_ENV = "VEXA_MODEL_PROXY_SECRET"

GRANT_PREFIX = "mproxy:grant:"
ROUTE_PREFIX = "mproxy:route:"
UNIT_ROUTE_PREFIX = "mproxy:unit-route:"
COUNT_PREFIX = "mproxy:count:"

#: A sealed route outlives the longest worker (the runtime's agent profile: 3600 s) with room to spare.
ROUTE_TTL_SEC = 2 * 3600
#: How long a delivered message may wait behind the running turn before its own turn starts.
QUEUE_ALLOWANCE_SEC = 600
#: The turn ceilings the harnesses apply by default (``llm/openai_agent.py``), used when the
#: deployment sets none: a grant must outlive the longest run its turn can start.
_TURN_SEC_DEFAULT, _JOB_SEC_DEFAULT = 900, 3600
#: Model requests one turn may make. A chat turn makes one per tool call (40 by default) and a job a
#: few hundred at most; past this the turn is spending on something no turn needs.
DEFAULT_MAX_REQUESTS = 600

ANTHROPIC_API = "https://api.anthropic.com"
OPENAI_API = "https://api.openai.com/v1"

AUTH_NONE, AUTH_BEARER, AUTH_X_API_KEY, AUTH_OAUTH = "none", "bearer", "x-api-key", "oauth"
#: ``VEXA_MODEL_ROUTE`` as the worker reads it: a turn whose model calls go through the proxy (the
#: person's own endpoint keeps its own mark, ``subject``), or the deployment's subscription.
ROUTE_PROXY, ROUTE_SUBSCRIPTION = "proxy", "subscription"
SUB_CLAUDE, SUB_CODEX = "claude", "codex"

#: Every name a harness reads to choose its endpoint, credential, model or request dialect — what
#: :func:`confine` reads (the dispatch's stamp, else agent-api's own environment) and then stamps,
#: whole, into the worker env. The runtime forwards none of these.
ROUTE_ENV = (*mg.PROVIDER_CREDENTIAL_ENV,
             "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
             "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
             "VEXA_LLM_BASE_URL", "VEXA_LLM_MODEL", "VEXA_LLM_EXTRA_BODY", "OPENAI_BASE_URL",
             "VEXA_AGENT_MODEL", "VEXA_CODEX_MODEL", "VEXA_RUNNER", "VEXA_AGENT_MAX_OUTPUT_TOKENS")
_CLAUDE_TIERS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                 "ANTHROPIC_DEFAULT_HAIKU_MODEL")


# ── the route ──────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Route:
    """Where a turn's model requests go, with what, and within which scope."""

    base_url: str
    auth: str = AUTH_NONE                 # none | bearer | x-api-key | oauth
    credential: str = field(default="", repr=False)
    #: The deployment's SUBSCRIPTION, when that is the route: its model calls go from the vendor's own
    #: CLI straight to the vendor, never through the proxy (only its toolbelt does).
    subscription: str = ""                # "" | claude | codex
    #: The models this turn may ask for; empty = whatever the endpoint serves (nothing names one).
    models: tuple[str, ...] = ()
    #: The largest output a single request may ask for; 0 = the request's own.
    max_output_tokens: int = 0
    #: The toolbelt (the gateway's /mcp) and the unit's delegation token at spawn: the harness's MCP
    #: attachment points at this proxy with the turn's token, and the proxy presents the bearer.
    toolbelt: str = ""
    toolbelt_bearer: str = field(default="", repr=False)
    #: THE SPEND CAP the turn was admitted under (``control_plane.llm_budget``): who pays, and the
    #: deployment's USD caps per scope and period as they stood at dispatch ("user/day": 5.0, …).
    #: The proxy refuses a model request once the payer's ledger has reached any of them.
    payer: str = ""
    spend_caps: tuple = ()

    def as_dict(self) -> dict:
        return {"base_url": self.base_url, "auth": self.auth, "credential": self.credential,
                "subscription": self.subscription, "models": list(self.models),
                "max_output_tokens": self.max_output_tokens, "toolbelt": self.toolbelt,
                "toolbelt_bearer": self.toolbelt_bearer, "payer": self.payer,
                "spend_caps": [list(c) for c in self.spend_caps]}

    @classmethod
    def from_dict(cls, d: Mapping) -> "Route":
        return cls(base_url=str(d.get("base_url") or ""), auth=str(d.get("auth") or AUTH_NONE),
                   credential=str(d.get("credential") or ""),
                   subscription=str(d.get("subscription") or ""),
                   models=tuple(str(m) for m in d.get("models") or ()),
                   max_output_tokens=int(d.get("max_output_tokens") or 0),
                   toolbelt=str(d.get("toolbelt") or ""),
                   toolbelt_bearer=str(d.get("toolbelt_bearer") or ""),
                   payer=str(d.get("payer") or ""),
                   spend_caps=tuple((str(a), str(b), float(c)) for a, b, c in d.get("spend_caps") or ()))

    def with_spend(self, payer: str, caps: Mapping) -> "Route":
        """This route under ``payer``'s spend caps (``{(scope, period): usd | None}``)."""
        sealed = tuple((sc, pe, float(cap)) for (sc, pe), cap in sorted(caps.items()) if cap is not None)
        return Route(**{**self.__dict__, "payer": payer, "spend_caps": sealed})

    def with_toolbelt(self, endpoint: str, bearer: str) -> "Route":
        return Route(**{**self.__dict__, "toolbelt": endpoint, "toolbelt_bearer": bearer})


def _v(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def route_of(env: Mapping[str, str]) -> Route:
    """The route a worker env (before :func:`confine`) describes, read the way its harness reads it.

    claude-code: the CLI sends ``ANTHROPIC_AUTH_TOKEN`` as a bearer, else ``ANTHROPIC_API_KEY`` as
    ``x-api-key`` — to ``ANTHROPIC_BASE_URL``, else Anthropic; with neither, the route is the
    deployment's subscription (``CLAUDE_CODE_OAUTH_TOKEN``, else the subscription file). openai-agent: ``VEXA_LLM_API_KEY`` (else the
    ANTHROPIC_* token or key) as a bearer to ``VEXA_LLM_BASE_URL`` (else ``ANTHROPIC_BASE_URL``).
    codex: ``OPENAI_API_KEY``/``CODEX_API_KEY`` as a bearer to the OpenAI API, else the subscription
    file. A subscription route names no credential of its own here: it stays with the CLI."""
    runner = _v(env, "VEXA_RUNNER") or units.deployment_runner()
    cap = _v(env, "VEXA_AGENT_MAX_OUTPUT_TOKENS")
    max_out = int(cap) if cap.isdigit() else 0
    if runner == "openai-agent":
        key = (_v(env, "VEXA_LLM_API_KEY") or _v(env, "ANTHROPIC_AUTH_TOKEN")
               or _v(env, "ANTHROPIC_API_KEY"))
        # the worker hands the harness VEXA_AGENT_MODEL for every turn, and VEXA_LLM_MODEL is the
        # harness's own fallback when it is handed none (llm/openai_agent.py run_turn)
        model = _v(env, "VEXA_AGENT_MODEL") or _v(env, "VEXA_LLM_MODEL")
        return Route(base_url=_v(env, "VEXA_LLM_BASE_URL") or _v(env, "ANTHROPIC_BASE_URL"),
                     auth=AUTH_BEARER if key else AUTH_NONE, credential=key,
                     models=(model,) if model else (), max_output_tokens=max_out)
    if runner == "codex":
        key = _v(env, "OPENAI_API_KEY") or _v(env, "CODEX_API_KEY")
        agent_model = _v(env, "VEXA_AGENT_MODEL")
        model = _v(env, "VEXA_CODEX_MODEL") or (agent_model if not agent_model.lower().startswith("claude")
                                                 else "")
        if key:
            return Route(base_url=_v(env, "OPENAI_BASE_URL") or OPENAI_API, auth=AUTH_BEARER,
                         credential=key, models=(model,) if model else (), max_output_tokens=max_out)
        return Route(base_url="", auth=AUTH_OAUTH, subscription=SUB_CODEX,
                     models=(model,) if model else (), max_output_tokens=max_out)
    base = _v(env, "ANTHROPIC_BASE_URL") or ANTHROPIC_API
    models = tuple(dict.fromkeys(m for m in (_v(env, "VEXA_AGENT_MODEL"),
                                             *(_v(env, t) for t in _CLAUDE_TIERS)) if m))
    if _v(env, "ANTHROPIC_AUTH_TOKEN"):
        auth, cred, sub = AUTH_BEARER, _v(env, "ANTHROPIC_AUTH_TOKEN"), ""
    elif _v(env, "ANTHROPIC_API_KEY"):
        auth, cred, sub = AUTH_X_API_KEY, _v(env, "ANTHROPIC_API_KEY"), ""
    elif _v(env, "CLAUDE_CODE_OAUTH_TOKEN"):
        auth, cred, sub = AUTH_OAUTH, "", SUB_CLAUDE
    elif _v(env, "VEXA_MODEL_ROUTE") == "subject":
        # The person's own endpoint with no key of their own: nothing of the deployment's goes with it.
        auth, cred, sub = AUTH_NONE, "", ""
    else:
        auth, cred, sub = AUTH_OAUTH, "", SUB_CLAUDE
    return Route(base_url=base, auth=auth, credential=cred, subscription=sub, models=models,
                 max_output_tokens=max_out)


def model_in_scope(model: str, scope: tuple[str, ...]) -> bool:
    """Is ``model`` one the turn may ask for? An empty scope names none and allows any. A scope entry
    matches itself, its dated snapshots (``claude-sonnet-4-5`` → ``claude-sonnet-4-5-20250929``), and
    — for the claude CLI's family aliases (``sonnet``/``opus``/``haiku``) — any model of that family.
    A context-window suffix (``[1m]``) is the CLI's own and never sent, so it is ignored."""
    if not scope:
        return True
    m = (model or "").strip().lower()
    if not m:
        return False
    for entry in scope:
        e = entry.strip().lower().split("[", 1)[0]
        if not e:
            continue
        if m == e or m.startswith(e + "-"):
            return True
        if e in ("sonnet", "opus", "haiku") and e in m:
            return True
    return False


# ── sealing ────────────────────────────────────────────────────────────────────────────────────

def _seal_key(secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), b"vexa-model-proxy.v1:route", hashlib.sha256).digest()


def seal(secret: str, rid: str, route: Route) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    nonce = secrets.token_bytes(12)
    ct = AESGCM(_seal_key(secret)).encrypt(nonce, json.dumps(route.as_dict()).encode(), rid.encode())
    return base64.b64encode(nonce + ct).decode("ascii")


def unseal(secret: str, rid: str, blob: str) -> Route:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    raw = base64.b64decode(blob)
    plain = AESGCM(_seal_key(secret)).decrypt(raw[:12], raw[12:], rid.encode())
    return Route.from_dict(json.loads(plain))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── grants ─────────────────────────────────────────────────────────────────────────────────────

class Refused(Exception):
    """A request the proxy will not forward: ``status`` and a reason safe to show the caller."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Grants:
    """The routes and per-turn tokens, in Redis. agent-api writes them; the proxy reads, counts and
    revokes them. ``clock`` is injectable so expiry is testable without waiting."""

    def __init__(self, client, secret: str, *, clock: Callable[[], float] = time.time) -> None:
        if not secret:
            raise ValueError(f"{SECRET_ENV} is not set: a route cannot be sealed")
        self._r = client
        self._secret = secret
        self._clock = clock

    # agent-api's half
    def put_route(self, unit: str, route: Route) -> str:
        rid = secrets.token_hex(16)
        self._r.set(ROUTE_PREFIX + rid, seal(self._secret, rid, route), ex=ROUTE_TTL_SEC)
        self._r.set(UNIT_ROUTE_PREFIX + unit, rid, ex=ROUTE_TTL_SEC)
        return rid

    def unit_route(self, unit: str) -> Optional[str]:
        rid = self._r.get(UNIT_ROUTE_PREFIX + unit)
        rid = rid.decode() if isinstance(rid, bytes) else rid
        return rid if rid and self._r.exists(ROUTE_PREFIX + rid) else None

    def mint(self, unit: str, rid: str, *, ttl_sec: int, max_requests: int = DEFAULT_MAX_REQUESTS) -> str:
        token = "vxm_" + secrets.token_urlsafe(32)
        exp = int(self._clock()) + int(ttl_sec)
        rec = {"unit": unit, "route": rid, "exp": exp, "max_requests": int(max_requests)}
        self._r.set(GRANT_PREFIX + _digest(token), json.dumps(rec), ex=int(ttl_sec))
        # the route lives at least as long as the last token to it
        self._r.expire(ROUTE_PREFIX + rid, max(ROUTE_TTL_SEC, int(ttl_sec)))
        self._r.expire(UNIT_ROUTE_PREFIX + unit, max(ROUTE_TTL_SEC, int(ttl_sec)))
        return token

    def route(self, token: str) -> Optional[Route]:
        """The route a live token reaches (for tests and the operator's own checks), or None."""
        try:
            _rec, route = self._resolve(token)
        except Refused:
            return None
        return route

    # the proxy's half
    def _resolve(self, token: str) -> tuple[dict, Route]:
        token = (token or "").strip()
        if not token:
            raise Refused(401, "no model-proxy token")
        raw = self._r.get(GRANT_PREFIX + _digest(token))
        if not raw:
            raise Refused(401, "this token is not a live turn's (unknown, revoked or expired)")
        rec = json.loads(raw)
        if int(self._clock()) >= int(rec.get("exp") or 0):
            raise Refused(401, "this turn's token has expired")
        rid = str(rec.get("route") or "")
        blob = self._r.get(ROUTE_PREFIX + rid)
        if not blob:
            raise Refused(401, "this turn's model route is gone")
        try:
            route = unseal(self._secret, rid, blob.decode() if isinstance(blob, bytes) else blob)
        except Exception:  # noqa: BLE001 — a route that does not open is no route
            raise Refused(401, "this turn's model route cannot be opened") from None
        return rec, route

    def admit(self, token: str, model: Optional[str]) -> Route:
        """The route for one request with ``token`` asking for ``model`` (None: the request names
        none), or :class:`Refused`. Counts the request against the turn's ceiling."""
        rec, route = self._resolve(token)
        if route.subscription:
            raise Refused(403, "a subscription route's model calls go from the vendor's CLI, never "
                               "through the proxy")
        if model is not None and not model_in_scope(model, route.models):
            raise Refused(403, f"model {model!r} is outside this turn's scope")
        self._within_spend(route)
        key = COUNT_PREFIX + _digest(token)
        n = self._r.incr(key)
        if n == 1:
            self._r.expire(key, max(1, int(rec["exp"]) - int(self._clock())))
        if n > int(rec.get("max_requests") or DEFAULT_MAX_REQUESTS):
            raise Refused(429, "this turn has made all the model requests it may")
        return route

    def _within_spend(self, route: Route) -> None:
        """Refuse once the payer's ledger (``llm_budget``: the USD agent-api has recorded from each
        reported model call) has reached a cap the turn was admitted under. The same check
        ``Budget.admit`` makes before dispatch, made again before every model request of the turn."""
        if not (route.payer and route.spend_caps):
            return
        from control_plane import llm_budget  # noqa: PLC0415

        periods = llm_budget._periods(self._clock())
        keys = [llm_budget._key(sc, route.payer, pe, periods[pe][0]) for sc, pe, _cap in route.spend_caps]
        try:
            values = self._r.mget(keys)
        except Exception:  # noqa: BLE001 — a ledger that cannot be read is no headroom (fail closed)
            raise Refused(503, "the spend ledger cannot be read") from None
        for (scope, period, cap), raw in zip(route.spend_caps, values):
            try:
                used = float(raw or 0)
            except (TypeError, ValueError):
                used = 0.0
            if used >= cap:
                raise Refused(402, f"the {scope} {period} model spend cap (${cap:g}) is reached")

    def toolbelt(self, token: str) -> tuple[str, str]:
        """``(url, bearer)`` for one MCP request with ``token``: the unit's CURRENT delegation token
        (agent-api re-mints it before it expires, ``control_plane.delegation_refresh``), else the one
        sealed at spawn. MCP requests are not model requests: they do not count against the ceiling."""
        rec, route = self._resolve(token)
        if not route.toolbelt:
            raise Refused(404, "this turn has no toolbelt")
        from control_plane import delegation_revocation as dr  # noqa: PLC0415

        current = self._r.get(dr.CURRENT_PREFIX + str(rec.get("unit") or ""))
        current = current.decode() if isinstance(current, bytes) else current
        bearer = (current or "").strip() or route.toolbelt_bearer
        if not bearer:
            raise Refused(401, "this turn's toolbelt holds no delegation")
        return route.toolbelt, bearer

    def revoke(self, token: str) -> bool:
        d = _digest((token or "").strip())
        return bool(self._r.delete(GRANT_PREFIX + d, COUNT_PREFIX + d))


# ── the dispatch's half: take the route out of the worker env ──────────────────────────────────

def grant_ttl_sec(env: Mapping[str, str] = os.environ) -> int:
    """A token's lifetime: the longest run its turn can start (a job's ceiling, else a turn's), plus
    the wait behind the turn already running."""
    def _sec(name: str, default: int) -> int:
        raw = _v(env, name)
        try:
            return max(1, int(float(raw))) if raw else default
        except ValueError:
            return default
    return max(_sec("VEXA_AGENT_JOB_MAX_TURN_SEC", _JOB_SEC_DEFAULT),
               _sec("VEXA_AGENT_MAX_TURN_SEC", _TURN_SEC_DEFAULT)) + QUEUE_ALLOWANCE_SEC


def confine(env: dict, *, url: str, grants: Optional[Grants], unit: str, cold: bool,
            toolbelt: str = "", spend: Optional[tuple] = None,
            source: Mapping[str, str] = os.environ) -> str:
    """Take the model route out of a dispatched worker env, in place, and return the turn's token
    (``""`` when no token could be minted: the worker then holds no model credential at all).

    The route is read from what the dispatch stamped, falling back to agent-api's own environment
    (``source``) for a name it left absent — the deployment's route. A cold spawn seals that route
    for the unit; a message to a running worker gets a token to the route that worker runs."""
    eff = {n: (env[n] if n in env else source.get(n, "")) or "" for n in ROUTE_ENV}
    eff["VEXA_MODEL_ROUTE"] = env.get("VEXA_MODEL_ROUTE", "")
    bearer = (env.get("VEXA_MCP_DELEGATION_TOKEN") or "").strip()
    route = route_of(eff).with_toolbelt(toolbelt.strip() if bearer else "", bearer)
    if spend is not None:
        route = route.with_spend(*spend)
    token = ""
    if grants is not None and url:
        rid = None if cold else grants.unit_route(unit)
        if rid is None:
            rid = grants.put_route(unit, route)
        token = grants.mint(unit, rid, ttl_sec=grant_ttl_sec(source))
    base = (url.rstrip("/") + mg.LLM_PATH) if url else ""
    for name in mg.PROVIDER_CREDENTIAL_ENV:
        env[name] = ""
    env["OPENAI_BASE_URL"] = ""
    if route.subscription:
        # The vendor's own CLI reads the subscription — its OAuth token stamped here, or the file
        # the runtime mounts — and calls the vendor's own endpoint. No API key rides along.
        env["CLAUDE_CODE_OAUTH_TOKEN"] = eff.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        env["ANTHROPIC_BASE_URL"] = ""
        env["VEXA_LLM_BASE_URL"] = ""
        env["VEXA_MODEL_ROUTE"] = ROUTE_SUBSCRIPTION
    else:
        env["ANTHROPIC_BASE_URL"] = base
        env["VEXA_LLM_BASE_URL"] = base
        env["VEXA_MODEL_ROUTE"] = env.get("VEXA_MODEL_ROUTE") or ROUTE_PROXY
    for name in ("VEXA_LLM_MODEL", "VEXA_LLM_EXTRA_BODY", "VEXA_AGENT_MODEL", "VEXA_CODEX_MODEL",
                 *_CLAUDE_TIERS):
        if eff.get(name):
            env[name] = eff[name]
    # the harness the route was read for is the harness the worker runs
    runner = eff.get("VEXA_RUNNER") or units.deployment_runner()
    env["VEXA_RUNNER"] = runner
    pinned = eff.get("VEXA_AGENT_MODEL") or ""
    if pinned and runner == "claude-code":
        # every call the CLI makes — background ones included — asks for the turn's own model
        for name in _CLAUDE_TIERS:
            env[name] = eff.get(name) or pinned
    env[mg.URL_ENV] = url
    env[mg.TOKEN_ENV] = token
    return token


# ── the proxy ──────────────────────────────────────────────────────────────────────────────────

#: Request headers never forwarded: the caller's token, hop-by-hop headers, and what the proxy sets.
_DROP_REQUEST = frozenset({"authorization", "x-api-key", "host", "content-length", "connection",
                           "keep-alive", "transfer-encoding", "te", "upgrade", "cookie",
                           "proxy-authorization", "proxy-connection", "chatgpt-account-id"})
#: The answer is relayed decoded (the proxy asks the provider for an identity encoding, and decodes
#: whatever it gets anyway), so its encoding and length headers are the proxy's to set, not relayed.
_DROP_RESPONSE = frozenset({"connection", "keep-alive", "transfer-encoding", "te", "upgrade",
                            "set-cookie", "proxy-authenticate", "content-encoding", "content-length"})
#: The request fields that ask for an output allowance, by dialect.
_OUTPUT_FIELDS = ("max_tokens", "max_output_tokens", "max_completion_tokens")

def upstream_headers(route: Route, incoming: Mapping[str, str]) -> dict[str, str]:
    """The headers sent upstream: the caller's, less its token and hop-by-hop headers, plus the
    route's credential under the name the endpoint expects."""
    out = {k.lower(): v for k, v in incoming.items() if k.lower() not in _DROP_REQUEST}
    out["accept-encoding"] = "identity"
    if route.auth == AUTH_X_API_KEY and route.credential:
        out["x-api-key"] = route.credential
    elif route.auth == AUTH_BEARER and route.credential:
        out["authorization"] = f"Bearer {route.credential}"
    return out


def _token_of(headers: Mapping[str, str]) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("x-api-key") or "").strip()


def _shape_body(body: bytes, content_type: str, route: Route) -> tuple[Optional[str], bytes]:
    """(the model the request asks for — None when it names none — and the body to forward). An
    output allowance above the route's cap is lowered to it."""
    if "json" not in (content_type or "").lower() or not body:
        return None, body
    try:
        payload = json.loads(body)
    except ValueError:
        return None, body
    if not isinstance(payload, dict):
        return None, body
    model = payload.get("model")
    model = str(model) if isinstance(model, str) else None
    if route.max_output_tokens:
        changed = False
        for f in _OUTPUT_FIELDS:
            v = payload.get(f)
            if isinstance(v, int) and not isinstance(v, bool) and v > route.max_output_tokens:
                payload[f] = route.max_output_tokens
                changed = True
        if changed:
            body = json.dumps(payload).encode("utf-8")
    return model, body


def _peek_model(body: bytes, content_type: str) -> Optional[str]:
    return _shape_body(body, content_type, Route(base_url=""))[0]


def create_app(grants: Grants, *, transport=None, timeout: float = 600.0):
    """The proxy's ASGI app. ``transport`` (an ``httpx.AsyncBaseTransport``) is injectable so the
    whole path — authentication, scope, credential injection, streaming — runs offline in tests."""
    import httpx  # noqa: PLC0415
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.background import BackgroundTask  # noqa: PLC0415
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import JSONResponse, StreamingResponse  # noqa: PLC0415
    from starlette.routing import Route as HttpRoute  # noqa: PLC0415

    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(timeout, connect=15.0),
                               follow_redirects=False)

    def _refusal(exc: Refused) -> JSONResponse:
        # Both dialects read `error.message`; `type` names the refusal for a person reading a log.
        return JSONResponse({"type": "error", "error": {"type": "model_proxy_refused",
                                                        "message": f"model proxy: {exc.reason}"}},
                            status_code=exc.status)

    async def health(_request: Request):
        return JSONResponse({"status": "ok"})

    async def revoke(request: Request):
        token = _token_of({k.lower(): v for k, v in request.headers.items()})
        if not token:
            return _refusal(Refused(401, "no model-proxy token"))
        await run_in_threadpool(grants.revoke, token)
        return JSONResponse({"revoked": True})

    async def _relay(request: Request, url: str, headers: dict, body: bytes):
        if request.url.query:
            url += "?" + request.url.query
        req = client.build_request(request.method, url, headers=headers, content=body)
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("model proxy: upstream unreachable (%s)", type(exc).__name__)
            return _refusal(Refused(502, f"the upstream could not be reached ({type(exc).__name__})"))
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE}
        return StreamingResponse(resp.aiter_bytes(), status_code=resp.status_code, headers=out_headers,
                                 background=BackgroundTask(resp.aclose))

    async def forward(request: Request):
        headers = {k.lower(): v for k, v in request.headers.items()}
        token = _token_of(headers)
        body = await request.body()
        ctype = headers.get("content-type", "")
        try:
            route = await run_in_threadpool(grants.admit, token, _peek_model(body, ctype))
        except Refused as exc:
            logger.info("model proxy refused a request: %s", exc.reason)
            return _refusal(exc)
        if not route.base_url:
            return _refusal(Refused(502, "this turn's model route names no endpoint"))
        _model, body = _shape_body(body, ctype, route)
        path = request.path_params.get("path", "")
        url = route.base_url.rstrip("/") + "/" + path.lstrip("/")
        return await _relay(request, url, upstream_headers(route, headers), body)

    async def toolbelt(request: Request):
        """The harness's MCP attachment: the turn's token in, the unit's delegation bearer out."""
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            url, bearer = await run_in_threadpool(grants.toolbelt, _token_of(headers))
        except Refused as exc:
            logger.info("model proxy refused a toolbelt request: %s", exc.reason)
            return _refusal(exc)
        path = request.path_params.get("path", "")
        if path:
            url = url.rstrip("/") + "/" + path.lstrip("/")
        out = {k: v for k, v in headers.items() if k not in _DROP_REQUEST}
        out["authorization"] = f"Bearer {bearer}"
        out["accept-encoding"] = "identity"
        return await _relay(request, url, out, await request.body())

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await client.aclose()

    return Starlette(routes=[
        HttpRoute("/health", health, methods=["GET"]),
        HttpRoute(mg.REVOKE_PATH, revoke, methods=["POST"]),
        HttpRoute(mg.LLM_PATH + "/{path:path}", forward, methods=["GET", "POST"]),
        HttpRoute(mg.MCP_PATH, toolbelt, methods=["GET", "POST", "DELETE"]),
        HttpRoute(mg.MCP_PATH + "/{path:path}", toolbelt, methods=["GET", "POST", "DELETE"]),
    ], lifespan=lifespan)


def main() -> None:  # pragma: no cover — the service entrypoint
    import redis  # noqa: PLC0415
    import uvicorn  # noqa: PLC0415

    from shared.config import load_settings  # noqa: PLC0415

    settings = load_settings()
    secret = settings.model_proxy_secret.get_secret_value()
    client = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2,
                            socket_timeout=5)
    app = create_app(Grants(client, secret))
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=(settings.log_level or "info").lower())


if __name__ == "__main__":  # pragma: no cover
    main()
