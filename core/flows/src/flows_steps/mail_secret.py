"""The relay password, by REFERENCE: a `broker:mail/<name>` ref resolved through the credential broker.

PRD 0.13.4 S2 (E1, FD5): outbound mail is configured per install — host, port, user and From in the
deployment's environment, and the password through the credential broker, never as a raw value in an
environment variable. `VEXA_MAIL_SMTP_PASSWORD_REF=broker:mail/<name>` names it; the operator stores
it once inside the broker's container (`python -m credential_broker.mail_secret put <name>`, the value
on stdin). flows reads it with the key it already holds for the broker — the sensors role key
(`VEXA_FLOWS_SENSORS_BROKER_URL` + `VEXA_FLOWS_SENSORS_KEY_FILE`, ADR-0050) — from
`POST /api/internal/mail/smtp-credential`, which reads `service/mail/<name>` and nothing else, and
audits every read by name. The value is held in memory for five minutes and goes nowhere but the
SMTP login: it is never logged and never part of an error message (P14).

Precedence: a ref, when set, is the only source — a raw `VEXA_MAIL_SMTP_PASSWORD` beside it is
ignored with a warning, so a stale secret left in an `.env` cannot silently win. With no ref, the raw
value is read exactly as before (existing installs, Helm's Secret).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
import uuid

import flows_config
from flows_sensors import broker_assertion
from flows_steps import mail_transport as mt

log = logging.getLogger("flows.mail_secret")

PATH = "/api/internal/mail/smtp-credential"
CACHE_S = 300.0
TIMEOUT_S = 8.0
REF = re.compile(r"^broker:mail/([a-z0-9][a-z0-9_.-]{0,62})$")

_cache: dict = {}


def ref() -> str:
    return (flows_config.get("VEXA_MAIL_SMTP_PASSWORD_REF") or "").strip()


def smtp_password(*, host: str = "", port="", transport=None, now=time.monotonic) -> str:
    """The relay's AUTH password: the broker's value for the ref, else the raw env value, else "".

    Raises `MailTransportError` (kind `config`, not retried) for a malformed ref or an unconfigured
    broker, and (kind `connect`, retried) for a broker that did not answer."""
    r = ref()
    if not r:
        return flows_config.get("VEXA_MAIL_SMTP_PASSWORD") or ""
    m = REF.match(r)
    if not m:
        raise mt.MailTransportError("config", "smtp", host, port,
                                    "VEXA_MAIL_SMTP_PASSWORD_REF must be broker:mail/<name>")
    if flows_config.get("VEXA_MAIL_SMTP_PASSWORD"):
        log.warning("VEXA_MAIL_SMTP_PASSWORD is set beside VEXA_MAIL_SMTP_PASSWORD_REF; the ref wins")
    name = m.group(1)
    hit = _cache.get(name)
    if hit and now() - hit[1] < CACHE_S:
        return hit[0]
    value = _from_broker(name, host, port, transport)
    _cache[name] = (value, now())
    return value


def forget() -> None:
    """Drop the cached value (the relay refused it), so the next send reads it afresh."""
    _cache.clear()


def _from_broker(name: str, host, port, transport) -> str:
    base = (flows_config.get("VEXA_FLOWS_SENSORS_BROKER_URL") or "").rstrip("/")
    key_file = flows_config.get("VEXA_FLOWS_SENSORS_KEY_FILE") or ""
    if not base or not key_file:
        raise mt.MailTransportError(
            "config", "smtp", host, port,
            "VEXA_MAIL_SMTP_PASSWORD_REF names the credential broker, but flows has no broker "
            "(VEXA_FLOWS_SENSORS_BROKER_URL + VEXA_FLOWS_SENSORS_KEY_FILE)")
    try:
        key = broker_assertion.load_key(key_file)
    except broker_assertion.KeyUnavailable:
        raise mt.MailTransportError("config", "smtp", host, port,
                                    "the broker key file is not readable") from None
    raw = json.dumps({"name": name}, separators=(",", ":")).encode()
    header = broker_assertion.sign(key, role="sensors", actor="flows-mail",
                                   session="mail-" + uuid.uuid4().hex, method="POST", path=PATH, body=raw)
    req = urllib.request.Request(base + PATH, data=raw, method="POST", headers={
        broker_assertion.HEADER: header, "Content-Type": "application/json"})
    opener = transport or urllib.request.urlopen
    try:
        with opener(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — the broker's fixed URL
            body = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise mt.MailTransportError(
                "config", "smtp", host, port,
                f"the credential broker holds no mail credential named {name!r} — store it with "
                f"`python -m credential_broker.mail_secret put {name}` in the broker container") from None
        raise mt.MailTransportError("config" if e.code in (401, 403) else "connect", "smtp", host, port,
                                    f"the credential broker refused the mail credential read ({e.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise mt.MailTransportError("connect", "smtp", host, port,
                                    f"the credential broker could not be reached ({type(e).__name__})") from None
    value = body.get("value") if isinstance(body, dict) else None
    if not isinstance(value, str) or not value:
        raise mt.MailTransportError("config", "smtp", host, port,
                                    "the credential broker answered without a mail credential")
    return value
