"""The mail TRANSPORT — how flows reaches a mailbox server, and the one typed error it fails with.

Two wires use this: the IMAP poller (`flows_integrations.inbox.ImapInbox`, reading invites) and the
SMTP sender (`flows_steps.emailx`, RSVPs, acknowledgements, minutes). Both used to assume Gmail:
IMAP was `imap.gmail.com` with no way to say otherwise, and a failure of either surfaced as
whatever exception the standard library raised, caught by a `poll hiccup` print or a generic
`unexpected:` reason. An on-premises Exchange mailbox fails in three ways an operator must tell
apart at a glance — the password, the certificate, the network — and each needs a different
person to fix it.

THE SETTINGS (all declared in `flows_config.DECLARED`; every one has a default that is the Gmail
preset, so a deployment that names none of them behaves exactly as before):

    IMAP  VEXA_MAIL_IMAP_HOST     imap.gmail.com      the IMAP server
          VEXA_MAIL_IMAP_PORT     993 / 143           993 for `tls`, 143 for `starttls` and `none`
          VEXA_MAIL_IMAP_TLS      tls                 tls (implicit) · starttls · none (lab only)
          VEXA_MAIL_IMAP_USER     = VEXA_MAIL_ADDR    the login name, when it is not the address
          VEXA_MAIL_IMAP_FOLDER   INBOX               the folder invites arrive in
          VEXA_MAIL_IMAP_CA_FILE  (system store)      a PEM bundle to trust instead
    SMTP  VEXA_MAIL_SMTP_TLS      auto                auto · tls · starttls · none
          VEXA_MAIL_SMTP_CA_FILE  (system store)      a PEM bundle to trust instead

The IMAP password is `VEXA_MAIL_APP_PASSWORD`, as it always was.

THE ERROR. `MailTransportError` is a `StepError`, so a send that fails inside a flow step lands on
the reaction as its `reason` — `mail:auth smtp mail.example.org:587 — …` — and the loop's retry
policy reads `retryable` from it: a wrong password or an untrusted certificate will not fix
itself on the next attempt, so those fail the reaction at once; a refused connection or a
timeout is retried. The message names the KIND, the WIRE, the HOST and PORT, and the server's
own words; it never contains a credential (P14), because nothing that builds it is handed one.
"""
from __future__ import annotations

import base64
import imaplib
import os
import re
import smtplib
import socket
import ssl

import flows_config
from flows.model import StepError

#: The kinds, and whether each is worth retrying. `config` is ours (a value that cannot work);
#: `protocol` is a server that answered something we did not expect.
KINDS = {"auth": False, "tls": False, "config": False, "connect": True, "protocol": True}

IMAP_TLS_MODES = ("tls", "starttls", "none")
SMTP_TLS_MODES = ("auto", "tls", "starttls", "none")

TIMEOUT_S = 20


class MailTransportError(StepError):
    """One mail-server failure, typed (P18). `kind` ∈ KINDS; `wire` is `imap` or `smtp`."""

    def __init__(self, kind: str, wire: str, host: str, port: int | str, detail: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown mail transport fault kind {kind!r}")
        self.kind, self.wire, self.host, self.port = kind, wire, host, port
        self.detail = _clean(detail)
        super().__init__(f"mail:{kind} {wire} {host}:{port} — {self.detail}",
                         retryable=KINDS[kind])

    def as_status(self) -> dict:
        """The shape the mailbox writes for its readiness probe — no credential, by construction."""
        return {"kind": self.kind, "wire": self.wire, "host": self.host, "port": str(self.port),
                "detail": self.detail}


def _clean(detail) -> str:
    if isinstance(detail, bytes):
        detail = detail.decode(errors="replace")
    if isinstance(detail, (list, tuple)):
        detail = " ".join(d.decode(errors="replace") if isinstance(d, bytes) else str(d)
                          for d in detail)
    return " ".join(str(detail).split())[:300]


# ── TLS ───────────────────────────────────────────────────────────────────────────────────────────

def tls_context(wire: str, host: str, port, ca_file: str, *, insecure: bool = False) -> ssl.SSLContext:
    """The certificate check every encrypted mail connection makes.

    `ca_file` set: trust THAT bundle and nothing else — an internal CA is what an on-premises
    Exchange is signed by, and adding it to the system store would also trust every public CA for
    a server that should only ever present the internal one. Unset: the system store. A named file
    that does not exist is a `config` fault now, not a TLS fault later."""
    if ca_file and not os.path.isfile(ca_file):
        raise MailTransportError("config", wire, host, port,
                                 f"CA bundle {ca_file} does not exist or is not a file")
    try:
        ctx = ssl.create_default_context(cafile=ca_file or None)
    except (ssl.SSLError, OSError) as e:
        raise MailTransportError("config", wire, host, port,
                                 f"CA bundle {ca_file} could not be loaded: {e}") from e
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def classify(exc: BaseException, wire: str, host: str, port, *, phase: str,
             secrets: tuple = ()) -> MailTransportError:
    """A standard-library exception → the typed fault. `phase` is `connect`, `tls` or `login`, so
    an `IMAP4.error` raised by LOGIN reads as a credential problem and the same class raised by
    STARTTLS reads as a TLS one.

    `secrets` are the credentials the failed call was handed. A server's refusal is quoted in the
    fault, and some servers quote the line they refused — for AUTH LOGIN that line is base64 of a
    credential. Each secret, raw and base64-encoded, is masked before the text goes anywhere (P14)."""
    err = _classify(exc, wire, host, port, phase=phase)
    detail = err.detail
    for value in secrets:
        if not value:
            continue
        for form in (value, base64.b64encode(value.encode()).decode()):
            detail = detail.replace(form, "***")
    if detail == err.detail:
        return err
    return MailTransportError(err.kind, err.wire, err.host, err.port, detail)


def _classify(exc: BaseException, wire: str, host: str, port, *, phase: str) -> MailTransportError:
    if isinstance(exc, MailTransportError):
        return exc
    if isinstance(exc, ssl.SSLCertVerificationError):
        return MailTransportError("tls", wire, host, port,
                                  f"server certificate not trusted: {exc.verify_message or exc}")
    if isinstance(exc, ssl.SSLError):
        return MailTransportError("tls", wire, host, port, f"TLS handshake failed: {exc}")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailTransportError("auth", wire, host, port,
                                  f"login refused ({exc.smtp_code}): {_clean(exc.smtp_error)}")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return MailTransportError("tls" if phase == "tls" else "protocol", wire, host, port, str(exc))
    # imaplib carries the server's words as BYTES in args[0]; `str(exc)` would render `b'…'`.
    said = _clean(exc.args[0]) if getattr(exc, "args", None) else str(exc)
    if isinstance(exc, imaplib.IMAP4.error) and phase == "tls":
        # `imaplib.starttls` raises `abort` (a subclass of `error`) for a server that does not
        # offer STARTTLS — a TLS answer, not a dropped connection.
        return MailTransportError("tls", wire, host, port, said)
    if isinstance(exc, imaplib.IMAP4.abort):
        return MailTransportError("connect", wire, host, port, f"connection dropped: {said}")
    if isinstance(exc, imaplib.IMAP4.error):
        kind = {"login": "auth", "tls": "tls"}.get(phase, "protocol")
        return MailTransportError(kind, wire, host, port, said)
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return MailTransportError("connect", wire, host, port, f"connection dropped: {exc}")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return MailTransportError("connect", wire, host, port, f"timed out after {TIMEOUT_S}s")
    if isinstance(exc, socket.gaierror):
        return MailTransportError("connect", wire, host, port, f"host name does not resolve: {exc}")
    if isinstance(exc, OSError):
        return MailTransportError("connect", wire, host, port,
                                  f"{type(exc).__name__}: {exc.strerror or exc}")
    if isinstance(exc, smtplib.SMTPException):
        return MailTransportError("protocol", wire, host, port, str(exc))
    return MailTransportError("protocol", wire, host, port, f"{type(exc).__name__}: {exc}")


# ── IMAP ──────────────────────────────────────────────────────────────────────────────────────────

def imap_settings() -> dict:
    """The IMAP endpoint this deployment reads. Refuses a mode it does not know, by name."""
    host = flows_config.get("VEXA_MAIL_IMAP_HOST")
    mode = flows_config.get("VEXA_MAIL_IMAP_TLS").lower()
    raw_port = flows_config.get("VEXA_MAIL_IMAP_PORT")
    if mode not in IMAP_TLS_MODES:
        raise MailTransportError("config", "imap", host, raw_port or "?",
                                 f"VEXA_MAIL_IMAP_TLS={mode!r} — expected one of "
                                 + ", ".join(IMAP_TLS_MODES))
    try:
        port = int(raw_port) if raw_port else (993 if mode == "tls" else 143)
    except ValueError:
        raise MailTransportError("config", "imap", host, raw_port,
                                 "VEXA_MAIL_IMAP_PORT is not a number") from None
    return {"host": host, "port": port, "tls": mode,
            "user": flows_config.get("VEXA_MAIL_IMAP_USER"),
            "folder": flows_config.get("VEXA_MAIL_IMAP_FOLDER"),
            "ca_file": flows_config.get("VEXA_MAIL_IMAP_CA_FILE")}


def imap_open(addr: str, password: str, settings: dict | None = None):
    """Connect, secure, log in, select — each step's failure typed. Returns the selected session.

    `none` sends the password in the clear and exists for a lab server only; it is honoured
    because an operator named it, and announced on every connection so it cannot be forgotten."""
    s = settings or imap_settings()
    host, port, mode = s["host"], s["port"], s["tls"]
    user = s["user"] or addr
    im = None
    try:
        if mode == "tls":
            ctx = tls_context("imap", host, port, s["ca_file"])
            im = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=TIMEOUT_S)
        else:
            im = imaplib.IMAP4(host, port, timeout=TIMEOUT_S)
    except BaseException as e:  # noqa: BLE001 — every failure leaves here typed
        raise classify(e, "imap", host, port, phase="connect") from e
    try:
        if mode == "starttls":
            try:
                im.starttls(ssl_context=tls_context("imap", host, port, s["ca_file"]))
            except BaseException as e:  # noqa: BLE001
                raise classify(e, "imap", host, port, phase="tls") from e
        elif mode == "none":
            print(f"mailbox WARNING: IMAP to {host}:{port} is unencrypted "
                  "(VEXA_MAIL_IMAP_TLS=none) — the password crosses the network in the clear; "
                  "lab use only", flush=True)
        try:
            im.login(_astring(user), password)
        except BaseException as e:  # noqa: BLE001
            raise classify(e, "imap", host, port, phase="login", secrets=(password,)) from e
        try:
            typ, data = im.select(s["folder"])
        except BaseException as e:  # noqa: BLE001
            raise classify(e, "imap", host, port, phase="select") from e
        if typ != "OK":
            raise MailTransportError("config", "imap", host, port,
                                     f"folder {s['folder']!r} cannot be selected: {_clean(data)}")
        return im
    except BaseException:
        try:
            im.shutdown()
        except Exception:  # noqa: BLE001 — closing a broken session cannot add information
            pass
        raise


_ATOM = re.compile(r"[A-Za-z0-9._@+-]+")


def _astring(value: str) -> str:
    """The IMAP login name as an RFC 3501 astring. `imaplib` quotes the password and sends the
    user verbatim, so an Exchange `DOMAIN\\user` went out as a bare atom containing a backslash —
    which the grammar does not allow. A plain address or UPN is sent unchanged."""
    if _ATOM.fullmatch(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── SMTP ──────────────────────────────────────────────────────────────────────────────────────────

def smtp_tls_mode() -> str:
    """`auto` keeps the 0.13.2 behaviour exactly: implicit TLS when VEXA_MAIL_SMTP_SECURE is on,
    else STARTTLS whenever the relay offers it. `tls`/`starttls` REQUIRE encryption (a relay that
    does not offer STARTTLS is refused, not used in the clear); `none` never upgrades."""
    host = flows_config.get("VEXA_MAIL_SMTP_HOST")
    mode = flows_config.get("VEXA_MAIL_SMTP_TLS").lower()
    if mode not in SMTP_TLS_MODES:
        raise MailTransportError("config", "smtp", host, flows_config.get("VEXA_MAIL_SMTP_PORT"),
                                 f"VEXA_MAIL_SMTP_TLS={mode!r} — expected one of "
                                 + ", ".join(SMTP_TLS_MODES))
    if mode == "auto" and flows_config.get_bool("VEXA_MAIL_SMTP_SECURE"):
        return "tls"
    return mode
