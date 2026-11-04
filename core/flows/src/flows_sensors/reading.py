"""The `reading` sensor kind — ONE generic sensor: a connection, what to read on it, an interval, a cursor.

Founder, 1 Nov 2026: a sensor is one generic primitive; Discord, GitHub, Gmail, Stripe or a product's
usage are CONNECTIONS, not kinds. So this kind knows no provider. A sensor of it names
`connection_id = "<connection>/<reading>"`; both are operator config (as the mailbox's `VEXA_MAIL_*`
are), read from the JSON file `VEXA_FLOWS_CONNECTIONS_FILE`:

    {"connections": {"<name>": {"transport": "http_json", "base_url": "https://…",
                                "auth": "bearer" | "none", "secret_env": "<ENV NAME>"}},
     "readings":    {"<name>/<reading>": {
         "path": "/v1/things", "params": {"since": "{cursor}", "limit": "100"},
         "items": "data",                     # dotted path to the list in the answer ("" = the answer)
         "key": "id",                         # the item field that names it (dedupe)
         "cursor": "created",                 # the item field the cursor follows (monotonic)
         "cursor_type": "epoch" | "text",     # epoch: numbers, the first read starts at now - first_window_s
         "first_window_s": 86400,             # how far a first read (and sensor_test) looks back, ≤ 1 day
         "where": {"status": "succeeded"},    # optional: keep only items whose fields equal these
         "keep": {"email": "billing_details.email", "amount": "amount"},   # ref name → dotted item path
         "event_type": "business.paid"}}}

Why the reading is operator config and not the sensor owner's free text: a sensor may read a
production system; its owner chooses WHICH declared reading runs, never writes the request.

What a tick does — no model, read-only by construction:
  * one GET (the only verb this kind has) to `base_url + path`, the cursor substituted into params;
    the secret is read from the process env by NAME at call time — never stored, never in a ref, an
    error or a log;
  * each item that passes `where` becomes one `Change(event_type, key, refs)`; refs = the `keep`
    fields plus `_evidence` {connection, reading, path, read_at} — where the reading came from, so a
    step that writes it onto a record can cite it;
  * the next cursor is the largest cursor value seen (unchanged when nothing new).
One page per tick: a reading is sized (`limit`) for its interval.
"""
from __future__ import annotations

import json
import os
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


def _dig(obj, path: str):
    for part in [p for p in (path or "").split(".") if p]:
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
    if not spec.get("path") or not spec.get("key") or not spec.get("event_type"):
        raise SensorFault(f"reading '{connection_id}' lacks path, key or event_type", retryable=False)
    return conn, spec


def http_get(conn: dict, path: str, params: dict) -> object:
    url = conn["base_url"].rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json"}
    if conn.get("auth") == "bearer":
        secret = os.environ.get(conn.get("secret_env") or "") or ""
        if not secret:
            raise SensorFault("the connection's secret is not set on this deployment", retryable=False)
        headers["Authorization"] = "Bearer " + secret
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — operator-declared URL
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SensorFault(f"read failed ({e.code})", retryable=e.code not in (401, 403)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SensorFault("the connection could not be read") from None


class ReadingSensor(SensorKind):
    kind = "reading"
    event_types = ("business.*",)
    min_interval_s = 60.0
    connection = "operator_connection"
    scopes = ("instance",)

    def __init__(self, get: Optional[Callable] = None, config: Optional[Callable] = None,
                 clock: Callable = time.time):
        self._get = get or http_get
        self._config = config or load_config
        self._now = clock

    def tick(self, sensor: SensorRow, cursor: Optional[str]) -> Tick:
        conn, spec = resolve(self._config(), sensor.connection_id or "")
        now = self._now()
        epoch = spec.get("cursor_type", "epoch") == "epoch"
        window = min(float(spec.get("first_window_s") or MAX_WINDOW_S), MAX_WINDOW_S)
        since = cursor if cursor is not None else (str(int(now - window)) if epoch else "")
        params = {k: str(v).replace("{cursor}", since) for k, v in (spec.get("params") or {}).items()}
        params = {k: v for k, v in params.items() if v != ""}
        answer = self._get(conn, spec["path"], params)
        items = _dig(answer, spec.get("items", "")) if spec.get("items") else answer
        if not isinstance(items, list):
            raise SensorFault("the answer has no item list")
        where = spec.get("where") or {}
        cf = spec.get("cursor") or ""
        evidence = {"connection": sensor.connection_id.split("/", 1)[0], "reading": sensor.connection_id,
                    "path": spec["path"], "read_at": now}
        best = cursor
        changes = []
        for it in items:
            if not isinstance(it, dict) or _dig(it, spec["key"]) in (None, ""):
                continue
            c = _dig(it, cf) if cf else None
            if c is not None:
                if since and (float(c) <= float(since) if epoch else str(c) <= since):
                    continue                               # the provider answered something already read
                if best is None or (float(c) > float(best) if epoch else str(c) > best):
                    best = str(int(c)) if epoch else str(c)
            if any(_dig(it, k) != v for k, v in where.items()):
                continue
            refs = {name: _dig(it, p) for name, p in (spec.get("keep") or {}).items()}
            refs["_evidence"] = evidence
            changes.append(Change(spec["event_type"], str(_dig(it, spec["key"])), refs))
        return Tick(changes=changes, cursor=best if best != cursor else None)
