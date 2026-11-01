"""The `reading` sensor kind — ONE generic sensor: a connection, what to read on it, an interval, a cursor.

Founder, 1 Nov 2026: a sensor is one generic primitive; Discord, GitHub, Gmail, Stripe or a product's
usage are CONNECTIONS, not kinds. So this kind knows no provider. A sensor of it names
`connection_id = "<connection>/<reading>"`; both are operator config (as the mailbox's `VEXA_MAIL_*`
are), read from the JSON file `VEXA_FLOWS_CONNECTIONS_FILE`:

    {"connections": {"<name>": {"transport": "http_json", "base_url": "https://…",
                                "auth": "bearer" | "oauth_refresh" | "none", "secret_env": "<ENV NAME>",
                                "scheme": "Bearer"}},   # bearer: the Authorization scheme (Discord: "Bot")
    # oauth_refresh (Google): {"token_url": "https://oauth2.googleapis.com/token", "client_id_env": …,
    #   "client_secret_env": …, "refresh_token_env": …}: an access token is exchanged for the refresh token
    #   (the one POST, to the token endpoint, never to the source) and kept in this process until it expires.
     "readings":    {"<name>/<reading>": {
         "path": "/v1/things", "params": {"since": "{cursor}", "limit": "100"},
         "items": "data",                     # dotted path to the list in the answer ("" = the answer)
         "key": "id",                         # the item field that names it (dedupe); a list of fields
                                              # joins them with "@" (an item read again once it changed:
                                              # ["html_url", "updated_at"])
         "cursor": "created",                 # the item field the cursor follows (monotonic)
         "cursor_type": "epoch" | "iso" | "text",  # epoch: numbers, the first read starts at now -
                                              # first_window_s; iso: ISO-8601 UTC times (…Z), compared as
                                              # text, the first read starts there too; text: from ""
         "first_window_s": 86400,             # how far a first read (and sensor_test) looks back, ≤ 1 day
         "empty_when_absent": true,           # optional: an answer without the item list reads as empty
         "detail": {"path": "/x/{id}", "params": {}, "max": 20},  # optional: one more GET per new item (its
                                              # key's first field fills {id}), merged into the item before
                                              # `keep` (Gmail lists only ids); at most `max` per tick
         "first": "baseline",                 # optional: the first read only sets the cursor and emits
                                              # nothing (a feed whose cursor is an id, not a time: Discord)
         "where": {"status": "succeeded"},    # optional: keep only items whose fields equal these
         "keep": {"email": "billing_details.email", "amount": "amount"},   # ref name → dotted item path
         "event_type": "business.paid",
         "method": "POST", "body": {...}}}} # optional, founder 1 Nov 2026 Q4 «Allow POST»: a declared report query.
                                              # Only to POST_ALLOWED (GA4 runReport, Search Console
                                              # searchAnalytics.query), only on a connection whose Google
                                              # grant is read-only (READ_ONLY_SCOPES); any other POST is refused.

Why the reading is operator config and not the sensor owner's free text: a sensor may read a
production system; its owner chooses WHICH declared reading runs, never writes the request.

What a tick does — no model, read-only by construction:
  * one GET (the only verb this kind has) to `base_url + path`, the cursor substituted into params;
    the secret is read from the process env by NAME at call time — never stored, never in a ref, an
    error or a log;
  * each item that passes `where` becomes one `Change(event_type, key, refs)`; refs = the `keep`
    fields plus `_evidence` {connection, reading, path, query, cursor, read_at} — where the reading
    came from (the query is the path with its params, never a credential; the cursor the read
    started from), so a step that writes it onto a record can cite it;
  * the next cursor is the largest cursor value seen (unchanged when nothing new).
One page per tick: a reading is sized (`limit`) for its interval.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

import flows_config

from .base import Change, SensorFault, SensorKind, SensorRow, Tick

TIMEOUT_S = 20.0
MAX_WINDOW_S = 86400.0
TRANSPORTS = ("http_json",)
#: Every read names itself (Discord refuses the library's default agent with 403).
USER_AGENT = "vexa-flows-sensor (read only)"
CURSOR_TYPES = ("epoch", "iso", "text")


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _key(item, key) -> str:
    """The item's dedupe key: one field, or several joined with "@"; '' when any is missing."""
    parts = [_dig(item, k) for k in (key if isinstance(key, list) else [key])]
    return "" if any(p in (None, "") for p in parts) else "@".join(str(p) for p in parts)


def _dig(obj, path: str):
    """A dotted path into an answer; a number steps into a list (GA4 rows: ``dimensionValues.0.value``)."""
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)] if int(part) < len(obj) else None
            continue
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def load_config(path: str = "") -> dict:
    path = path or flows_config.get("VEXA_FLOWS_CONNECTIONS_FILE") or ""
    if not path:
        raise SensorFault("no connections are configured on this deployment", retryable=False)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        raise SensorFault("the connections file cannot be read", retryable=False) from None
    return cfg if isinstance(cfg, dict) else {}


def resolve(cfg: dict, connection_id: str) -> tuple[dict, dict]:
    """(connection, reading) for `<connection>/<reading>`, or a SensorFault naming what is missing."""
    name = (connection_id or "").split("/", 1)[0]
    conn = (cfg.get("connections") or {}).get(name)
    spec = (cfg.get("readings") or {}).get(connection_id or "")
    if not conn or not spec:
        raise SensorFault(f"unknown reading '{connection_id}'", retryable=False)
    if conn.get("transport") not in TRANSPORTS:
        raise SensorFault(f"unsupported transport on '{name}'", retryable=False)
    if conn.get("auth", "none") not in AUTHS:
        raise SensorFault(f"unsupported auth on '{name}'", retryable=False)
    if not spec.get("path") or not spec.get("key") or not spec.get("event_type"):
        raise SensorFault(f"reading '{connection_id}' lacks path, key or event_type", retryable=False)
    if spec.get("cursor_type", "epoch") not in CURSOR_TYPES:
        raise SensorFault(f"reading '{connection_id}' has an unknown cursor_type", retryable=False)
    return conn, spec


AUTHS = ("bearer", "oauth_refresh", "service_account", "none")
#: The only POST a reading may make (founder 1 Nov 2026, Q4): two report queries that read and change nothing.
POST_ALLOWED = (
    re.compile(r"^https://analyticsdata\.googleapis\.com/v1beta/properties/\d+:runReport$"),
    re.compile(r"^https://(?:www\.googleapis\.com|searchconsole\.googleapis\.com)/webmasters/v3/sites/[^/?#]+"
               r"/searchAnalytics/query$"),
)
#: ... and only with a Google grant that can read and nothing else.
READ_ONLY_SCOPES = frozenset({"https://www.googleapis.com/auth/analytics.readonly",
                              "https://www.googleapis.com/auth/webmasters.readonly"})
OAUTH_ENVS = ("client_id_env", "client_secret_env", "refresh_token_env")
_TOKENS: dict = {}


def secret_envs(conn: dict) -> list[str]:
    """The env NAMES a connection reads its secret from (never their values)."""
    if conn.get("auth") == "bearer":
        return [conn.get("secret_env") or ""]
    if conn.get("auth") == "service_account":
        return [conn.get("key_env") or ""]
    if conn.get("auth") == "oauth_refresh":
        return [conn.get(k) or "" for k in OAUTH_ENVS]
    return []


def secret_set(conn: dict) -> bool:
    return all(n and os.environ.get(n) for n in secret_envs(conn))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _service_account_token(conn: dict, now: Optional[float] = None) -> tuple[str, frozenset]:
    """A Google access token for a service account (its JSON key in the env var ``key_env``, plain or base64): a JWT
    signed RS256 asking for exactly the connection's ``scopes``, exchanged at the key's token_uri. Kept until 60 s
    before it expires. Answers (token, the scopes granted)."""
    now = time.time() if now is None else now
    raw = os.environ.get(conn.get("key_env") or "") or ""
    if not raw:
        raise SensorFault("the connection's secret is not set on this deployment", retryable=False)
    try:
        info = json.loads(raw if raw.lstrip().startswith("{") else base64.b64decode(raw))
    except (ValueError, TypeError):
        raise SensorFault("the service account key cannot be read", retryable=False) from None
    scopes = " ".join(conn.get("scopes") or [])
    key = ("sa", info.get("client_email"), scopes)
    held = _TOKENS.get(key)
    if held and held[1] - 60 > now:
        return held[0], held[2]
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    head = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    uri = info.get("token_uri") or "https://oauth2.googleapis.com/token"
    claims = _b64u(json.dumps({"iss": info.get("client_email"), "scope": scopes, "aud": uri,
                               "iat": int(now), "exp": int(now) + 3600}).encode())
    pk = serialization.load_pem_private_key(str(info.get("private_key") or "").encode(), password=None)
    sig = _b64u(pk.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()))
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{head}.{claims}.{sig}"}).encode()
    out = _token_post(uri, body)
    granted = frozenset((out.get("scope") or scopes).split())
    _TOKENS[key] = (out["access_token"], now + float(out.get("expires_in") or 3600), granted)
    return out["access_token"], granted


def _token_post(url: str, body: bytes) -> dict:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — operator-declared URL
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SensorFault(f"token exchange failed ({e.code})", retryable=e.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SensorFault("the token endpoint could not be reached") from None
    if not out.get("access_token"):
        raise SensorFault("the token endpoint answered no access token", retryable=False)
    return out


def _access_token(conn: dict, now: Optional[float] = None) -> str:
    """An OAuth access token for the connection's refresh token, kept until 60 s before it expires."""
    now = time.time() if now is None else now
    if not secret_set(conn):
        raise SensorFault("the connection's secret is not set on this deployment", retryable=False)
    key = tuple(conn.get(k) for k in ("token_url",) + OAUTH_ENVS)
    held = _TOKENS.get(key)
    if held and held[1] - 60 > now:
        return held[0]
    body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                   "client_id": os.environ[conn["client_id_env"]],
                                   "client_secret": os.environ[conn["client_secret_env"]],
                                   "refresh_token": os.environ[conn["refresh_token_env"]]}).encode()
    req = urllib.request.Request(conn.get("token_url") or "", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — operator-declared URL
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SensorFault(f"token exchange failed ({e.code})", retryable=e.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SensorFault("the token endpoint could not be reached") from None
    tok = out.get("access_token")
    if not tok:
        raise SensorFault("the token endpoint answered no access token", retryable=False)
    _TOKENS[key] = (tok, now + float(out.get("expires_in") or 3600), frozenset((out.get("scope") or "").split()))
    return tok


def http_get(conn: dict, path: str, params: dict) -> object:
    url = conn["base_url"].rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if conn.get("auth") == "bearer":
        secret = os.environ.get(conn.get("secret_env") or "") or ""
        if not secret:
            raise SensorFault("the connection's secret is not set on this deployment", retryable=False)
        headers["Authorization"] = f"{conn.get('scheme') or 'Bearer'} {secret}"
    elif conn.get("auth") == "oauth_refresh":
        headers["Authorization"] = "Bearer " + _access_token(conn)
    elif conn.get("auth") == "service_account":
        headers["Authorization"] = "Bearer " + _service_account_token(conn)[0]
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — operator-declared URL
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SensorFault(f"read failed ({e.code})", retryable=e.code not in (401, 403)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SensorFault("the connection could not be read") from None


def granted_scopes(conn: dict) -> frozenset:
    """The scopes the connection's Google token was actually granted (a token is fetched if none is held)."""
    if conn.get("auth") == "service_account":
        return _service_account_token(conn)[1]
    if conn.get("auth") == "oauth_refresh":
        _access_token(conn)
        key = tuple(conn.get(k) for k in ("token_url",) + OAUTH_ENVS)
        return (_TOKENS.get(key) or (None, 0, frozenset()))[2]
    return frozenset()


def check_post(conn: dict, path: str) -> None:
    """Refuse every POST but the declared report queries on a read-only Google grant (founder 1 Nov 2026, Q4)."""
    url = conn["base_url"].rstrip("/") + "/" + path.lstrip("/")
    if not any(rx.match(url) for rx in POST_ALLOWED):
        raise SensorFault("a reading may POST only to a declared report query (GA4 runReport, Search Console query)",
                          retryable=False)
    if conn.get("auth") not in ("service_account", "oauth_refresh"):
        raise SensorFault("a report query needs a Google grant (service account or OAuth)", retryable=False)
    if conn.get("auth") == "service_account" and not set(conn.get("scopes") or []) <= READ_ONLY_SCOPES:
        raise SensorFault("a report query asks only for read-only scopes", retryable=False)
    granted = granted_scopes(conn)
    if not granted or not granted <= READ_ONLY_SCOPES:
        raise SensorFault("the connection's grant is not read-only", retryable=False)


def http_post(conn: dict, path: str, body: dict) -> object:
    """The one POST a reading makes: a declared report query (``check_post`` first), JSON in and out."""
    check_post(conn, path)
    url = conn["base_url"].rstrip("/") + "/" + path.lstrip("/")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT}
    headers["Authorization"] = "Bearer " + (_service_account_token(conn)[0] if conn.get("auth") == "service_account"
                                            else _access_token(conn))
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — allow-listed URL
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SensorFault(f"read failed ({e.code})", retryable=e.code not in (401, 403)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SensorFault("the connection could not be read") from None


def _fill(value, since: str):
    """``{cursor}`` substituted in every string of a declared body."""
    if isinstance(value, str):
        return value.replace("{cursor}", since)
    if isinstance(value, list):
        return [_fill(v, since) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, since) for k, v in value.items()}
    return value


class ReadingSensor(SensorKind):
    kind = "reading"
    event_types = ("business.*",)
    min_interval_s = 60.0
    connection = "operator_connection"
    scopes = ("instance",)

    def __init__(self, get: Optional[Callable] = None, config: Optional[Callable] = None,
                 clock: Callable = time.time, post: Optional[Callable] = None):
        self._get = get or http_get
        self._post = post or http_post
        self._config = config or load_config
        self._now = clock

    def tick(self, sensor: SensorRow, cursor: Optional[str]) -> Tick:
        conn, spec = resolve(self._config(), sensor.connection_id or "")
        now = self._now()
        ctype = spec.get("cursor_type", "epoch")
        epoch = ctype == "epoch"
        window = min(float(spec.get("first_window_s") or MAX_WINDOW_S), MAX_WINDOW_S)
        first = {"epoch": str(int(now - window)), "iso": _iso(now - window)}.get(ctype, "")
        since = cursor if cursor is not None else first
        params = {k: str(v).replace("{cursor}", since) for k, v in (spec.get("params") or {}).items()}
        params = {k: v for k, v in params.items() if v != ""}
        if str(spec.get("method") or "GET").upper() == "POST":
            if params:
                raise SensorFault("a report query carries its query in the body, not in params", retryable=False)
            answer = self._post(conn, spec["path"], _fill(spec.get("body") or {}, since))
        elif str(spec.get("method") or "GET").upper() != "GET":
            raise SensorFault("a reading only GETs, or POSTs a declared report query", retryable=False)
        else:
            answer = self._get(conn, spec["path"], params)
        items = _dig(answer, spec.get("items", "")) if spec.get("items") else answer
        if items is None and isinstance(answer, dict) and spec.get("empty_when_absent"):
            items = []                                     # the provider leaves an empty list out (Gmail)
        if not isinstance(items, list):
            raise SensorFault("the answer has no item list")
        where = spec.get("where") or {}
        cf = spec.get("cursor") or ""
        query = spec["path"] + ("?" + urllib.parse.urlencode(params) if params else "")
        evidence = {"connection": sensor.connection_id.split("/", 1)[0], "reading": sensor.connection_id,
                    "path": spec["path"], "query": query, "cursor": since, "read_at": now}
        best = cursor
        changes = []
        detailed = 0
        for it in items:
            if not isinstance(it, dict) or not _key(it, spec["key"]):
                continue
            c = _dig(it, cf) if cf else None
            if c is not None:
                if since and (float(c) <= float(since) if epoch else str(c) <= since):
                    continue                               # the provider answered something already read
                if best is None or (float(c) > float(best) if epoch else str(c) > best):
                    best = str(int(c)) if epoch else str(c)
            if any(_dig(it, k) != v for k, v in where.items()):
                continue
            det = spec.get("detail") or {}
            if det.get("path"):
                if detailed >= int(det.get("max") or 20):
                    break                                  # the rest is read on the next tick
                first = spec["key"][0] if isinstance(spec["key"], list) else spec["key"]
                ident = str(_dig(it, first) or "")
                if not ident or "/" in ident or "?" in ident:
                    continue
                more = self._get(conn, det["path"].replace("{id}", urllib.parse.quote(ident, safe="")),
                                 dict(det.get("params") or {}))
                detailed += 1
                if isinstance(more, dict):
                    it = {**it, **more}
            refs = {name: _dig(it, p) for name, p in (spec.get("keep") or {}).items()}
            refs["_evidence"] = evidence
            changes.append(Change(spec["event_type"], _key(it, spec["key"]), refs))
        if cursor is None and spec.get("first") == "baseline":
            changes = []                                   # first sight: where to start, nothing to tell
        return Tick(changes=changes, cursor=best if best != cursor else None)
