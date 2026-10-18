"""An IMAP mailbox that is not Gmail — on-premises Exchange — and the typed faults it fails with.

Every case here talks to a SCRIPTED IMAP SERVER on loopback, with certificates made per run by
`openssl`: a CA the server's certificate chains to, and a second, unrelated CA. Nothing is mocked
below `imaplib`, so the TLS handshake, the STARTTLS upgrade and the LOGIN line are the real ones.

What the previous code could not do, and therefore what fails on it:
  * read from any host but `imap.gmail.com` (the host was a class constant);
  * log in with a name other than the address;
  * trust an internal CA, or speak STARTTLS;
  * say WHICH thing went wrong — a wrong password, an untrusted certificate and an unreachable
    host all left as whatever `imaplib` raised, and the poll loop printed `poll hiccup`.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from flows_integrations import mailbox_status
from flows_integrations.inbox import ImapInbox, get_inbox
from flows_steps import mail_transport as mt
from flows.model import StepError

FIX = Path(__file__).parent / "mailpit"


@pytest.fixture(autouse=True)
def _current_transport_module():
    """Another module in this suite purges `flows_steps.*` from `sys.modules` to prove an import is
    side-effect free, after which the inbox's lazy import binds a FRESH `mail_transport` — and a
    `pytest.raises` on this file's stale class would miss the very error it is waiting for."""
    global mt
    import importlib
    mt = importlib.import_module("flows_steps.mail_transport")
ADDR = "vexa-bot@corp.example"
USER = "CORP\\vexa-bot"            # an Exchange down-level logon name: not the address
PASSWORD = "s3cret-Exchange-PW"


# ── certificates ─────────────────────────────────────────────────────────────────────────────────

def _openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("openssl is needed to mint the per-run certificates")
    d = tmp_path_factory.mktemp("pki")
    for ca in ("ca", "other-ca"):
        _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                 "-subj", f"/CN=test {ca}", "-keyout", f"{ca}.key", "-out", f"{ca}.pem", cwd=d)
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
             "-keyout", "server.key", "-out", "server.csr", cwd=d)
    (d / "san.ext").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    _openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
             "-CAcreateserial", "-days", "2", "-extfile", "san.ext", "-out", "server.pem", cwd=d)
    return d


# ── the scripted server ──────────────────────────────────────────────────────────────────────────

def _astrings(text: str) -> list[str]:
    """Parse IMAP astring arguments: atoms and quoted strings with backslash escapes."""
    out, i = [], 0
    while i < len(text):
        if text[i] == " ":
            i += 1
        elif text[i] == '"':
            j, buf = i + 1, []
            while text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
        else:
            j = text.find(" ", i)
            j = len(text) if j < 0 else j
            out.append(text[i:j])
            i = j
    return out


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        srv = self.server
        if srv.mode == "tls":
            self.request = srv.ctx.wrap_socket(self.request, server_side=True)
        super().setup()

    def _send(self, line: str | bytes):
        self.wfile.write(line if isinstance(line, bytes) else line.encode())
        self.wfile.flush()

    def handle(self):
        srv = self.server
        encrypted = srv.mode == "tls"
        self._send("* OK test IMAP ready\r\n")
        while True:
            try:
                raw = self.rfile.readline()
            except (ssl.SSLError, OSError):
                return
            if not raw:
                return
            tag, _, rest = raw.decode().rstrip("\r\n").partition(" ")
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            if verb == "CAPABILITY":
                caps = "IMAP4rev1" + (" STARTTLS" if srv.offer_starttls and not encrypted else "")
                self._send(f"* CAPABILITY {caps}\r\n{tag} OK done\r\n")
            elif verb == "STARTTLS":
                self._send(f"{tag} OK begin TLS\r\n")
                self.request = srv.ctx.wrap_socket(self.request, server_side=True)
                self.rfile = self.request.makefile("rb")
                self.wfile = self.request.makefile("wb")
                encrypted = True
            elif verb == "LOGIN":
                user, password = _astrings(args)
                srv.logins.append({"user": user, "encrypted": encrypted})
                if (user, password) == (srv.user, srv.password):
                    self._send(f"{tag} OK LOGIN completed\r\n")
                else:
                    self._send(f"{tag} NO LOGIN failed\r\n")
            elif verb == "SELECT":
                if _astrings(args)[0] in srv.folders:
                    self._send(f"* {len(srv.messages)} EXISTS\r\n{tag} OK [READ-WRITE] done\r\n")
                else:
                    self._send(f"{tag} NO no such mailbox\r\n")
            elif verb == "UID":
                sub, _, uargs = args.partition(" ")
                if sub.upper() == "SEARCH":
                    uids = sorted(srv.messages)
                    m = re.match(r"UID (\d+):\*", uargs)
                    if m:
                        uids = [u for u in uids if u >= int(m.group(1))] or uids[-1:]
                    self._send(f"* SEARCH {' '.join(map(str, uids))}\r\n{tag} OK done\r\n")
                else:                                   # FETCH <uid> (RFC822)
                    uid = int(uargs.split()[0])
                    body = srv.messages[uid]
                    self._send(f"* 1 FETCH (UID {uid} RFC822 {{{len(body)}}}\r\n".encode()
                               + body + f")\r\n{tag} OK done\r\n".encode())
            elif verb == "LOGOUT":
                self._send(f"* BYE\r\n{tag} OK done\r\n")
                return
            else:
                self._send(f"{tag} BAD unknown\r\n")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A client that refuses our certificate hangs up mid-handshake — the case under test."""


@pytest.fixture
def imap_server(pki):
    made = []

    def start(mode="tls", offer_starttls=True, messages=None, folders=("INBOX",)):
        srv = _Server(("127.0.0.1", 0), _Handler)
        srv.mode, srv.offer_starttls = mode, offer_starttls
        srv.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        srv.ctx.load_cert_chain(pki / "server.pem", pki / "server.key")
        srv.user, srv.password, srv.folders = USER, PASSWORD, set(folders)
        srv.messages, srv.logins = dict(messages or {}), []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv

    yield start
    for srv in made:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def exchange_env(monkeypatch, pki):
    """An on-premises Exchange shape: own host, own login name, internal CA."""
    for k in ("VEXA_MAIL_INBOX", "VEXA_MAIL_IMAP_PORT", "VEXA_MAIL_IMAP_TLS",
              "VEXA_MAIL_IMAP_FOLDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VEXA_MAIL_ADDR", ADDR)
    monkeypatch.setenv("VEXA_MAIL_APP_PASSWORD", PASSWORD)
    monkeypatch.setenv("VEXA_MAIL_IMAP_HOST", "127.0.0.1")
    monkeypatch.setenv("VEXA_MAIL_IMAP_USER", USER)
    monkeypatch.setenv("VEXA_MAIL_IMAP_CA_FILE", str(pki / "ca.pem"))
    return monkeypatch


def _use(env, srv, **extra):
    env.setenv("VEXA_MAIL_IMAP_PORT", str(srv.server_address[1]))
    for k, v in extra.items():
        env.setenv(k, v)


# ── the preset ───────────────────────────────────────────────────────────────────────────────────

def test_gmail_is_the_preset_when_nothing_is_named(monkeypatch):
    for k in ("VEXA_MAIL_IMAP_HOST", "VEXA_MAIL_IMAP_PORT", "VEXA_MAIL_IMAP_TLS",
              "VEXA_MAIL_IMAP_USER", "VEXA_MAIL_IMAP_FOLDER", "VEXA_MAIL_IMAP_CA_FILE"):
        monkeypatch.delenv(k, raising=False)
    s = mt.imap_settings()
    assert (s["host"], s["port"], s["tls"], s["user"], s["folder"], s["ca_file"]) == \
        ("imap.gmail.com", 993, "tls", "", "INBOX", "")


def test_the_port_follows_the_tls_mode_and_a_bad_mode_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_IMAP_PORT", raising=False)
    monkeypatch.setenv("VEXA_MAIL_IMAP_TLS", "starttls")
    assert mt.imap_settings()["port"] == 143
    monkeypatch.setenv("VEXA_MAIL_IMAP_TLS", "ssl3")
    with pytest.raises(mt.MailTransportError, match="VEXA_MAIL_IMAP_TLS='ssl3'") as ei:
        ImapInbox()
    assert ei.value.kind == "config"


# ── an on-premises Exchange over IMAP ────────────────────────────────────────────────────────────

def test_implicit_tls_with_an_internal_ca_and_a_separate_login_name(exchange_env, imap_server):
    srv = imap_server("tls")
    _use(exchange_env, srv)
    box = get_inbox()
    assert isinstance(box, ImapInbox) and box.host == "127.0.0.1"
    assert box.tail_cursor() == "0"
    assert srv.logins == [{"user": USER, "encrypted": True}]


def test_starttls_upgrades_before_the_password_is_sent(exchange_env, imap_server):
    srv = imap_server("plain", offer_starttls=True)
    _use(exchange_env, srv, VEXA_MAIL_IMAP_TLS="starttls")
    ImapInbox().tail_cursor()
    assert srv.logins == [{"user": USER, "encrypted": True}]


def test_an_invite_is_read_through_starttls_into_the_same_facts(exchange_env, imap_server):
    """The whole read path against a non-Gmail server: search past the cursor, fetch, parse the
    ICS. The facts are the ones the shared parse produces for any source."""
    raw = (FIX / "invite-exchange-teams.eml").read_bytes()
    srv = imap_server("plain", offer_starttls=True, messages={7: raw})
    _use(exchange_env, srv, VEXA_MAIL_IMAP_TLS="starttls")
    msgs = list(ImapInbox().fetch("6"))
    assert len(msgs) == 1 and msgs[0].cursor == "7" and msgs[0].ics
    assert "BEGIN:VCALENDAR" in msgs[0].ics


def test_a_custom_folder_is_selected_and_a_missing_one_is_a_config_fault(exchange_env, imap_server):
    srv = imap_server("tls", folders=("INBOX", "Vexa"))
    _use(exchange_env, srv, VEXA_MAIL_IMAP_FOLDER="Vexa")
    ImapInbox().tail_cursor()
    _use(exchange_env, srv, VEXA_MAIL_IMAP_FOLDER="Nope")
    with pytest.raises(mt.MailTransportError, match="folder 'Nope'") as ei:
        ImapInbox().tail_cursor()
    assert ei.value.kind == "config"


# ── the refusals, typed ──────────────────────────────────────────────────────────────────────────

def test_a_wrong_password_is_an_auth_fault_and_never_echoes_the_password(exchange_env, imap_server):
    srv = imap_server("tls")
    _use(exchange_env, srv, VEXA_MAIL_APP_PASSWORD="wrong-" + PASSWORD)
    with pytest.raises(mt.MailTransportError) as ei:
        ImapInbox().tail_cursor()
    e = ei.value
    assert e.kind == "auth" and e.wire == "imap" and not e.retryable
    assert str(e) == f"mail:auth imap 127.0.0.1:{srv.server_address[1]} — LOGIN failed"
    assert PASSWORD not in str(e) and PASSWORD not in json.dumps(e.as_status())


def test_a_certificate_from_another_ca_is_a_tls_fault_before_any_login(exchange_env, imap_server, pki):
    srv = imap_server("tls")
    _use(exchange_env, srv, VEXA_MAIL_IMAP_CA_FILE=str(pki / "other-ca.pem"))
    with pytest.raises(mt.MailTransportError, match="certificate not trusted") as ei:
        ImapInbox().tail_cursor()
    assert ei.value.kind == "tls" and not ei.value.retryable
    assert srv.logins == [], "the password must never reach a server we could not verify"


def test_the_wrong_ca_is_refused_on_starttls_too(exchange_env, imap_server, pki):
    srv = imap_server("plain", offer_starttls=True)
    _use(exchange_env, srv, VEXA_MAIL_IMAP_TLS="starttls",
         VEXA_MAIL_IMAP_CA_FILE=str(pki / "other-ca.pem"))
    with pytest.raises(mt.MailTransportError) as ei:
        ImapInbox().tail_cursor()
    assert ei.value.kind == "tls" and srv.logins == []


def test_starttls_required_refuses_a_server_that_does_not_offer_it(exchange_env, imap_server):
    srv = imap_server("plain", offer_starttls=False)
    _use(exchange_env, srv, VEXA_MAIL_IMAP_TLS="starttls")
    with pytest.raises(mt.MailTransportError) as ei:
        ImapInbox().tail_cursor()
    assert ei.value.kind == "tls" and srv.logins == []


def test_an_unreachable_host_is_a_connect_fault_and_retryable(exchange_env):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]           # released below: nothing listens there any more
    exchange_env.setenv("VEXA_MAIL_IMAP_PORT", str(port))
    with pytest.raises(mt.MailTransportError) as ei:
        ImapInbox().tail_cursor()
    assert ei.value.kind == "connect" and ei.value.retryable


def test_a_missing_ca_bundle_is_a_config_fault(exchange_env, imap_server, tmp_path):
    srv = imap_server("tls")
    _use(exchange_env, srv, VEXA_MAIL_IMAP_CA_FILE=str(tmp_path / "nope.pem"))
    with pytest.raises(mt.MailTransportError, match="does not exist") as ei:
        ImapInbox().tail_cursor()
    assert ei.value.kind == "config"


def test_the_fault_is_a_step_error_so_a_send_inside_a_flow_lands_on_the_reaction():
    """The loop records `str(StepError)` as the reaction's reason and reads `retryable` from it:
    an auth or TLS fault fails the reaction at once, a connect fault backs off and retries."""
    auth = mt.MailTransportError("auth", "smtp", "mail.corp.example", 587, "535 bad")
    down = mt.MailTransportError("connect", "smtp", "mail.corp.example", 587, "refused")
    assert isinstance(auth, StepError) and not auth.retryable and down.retryable
    assert str(auth) == "mail:auth smtp mail.corp.example:587 — 535 bad"


# ── the mailbox's health, for the readiness probe ───────────────────────────────────────────────

def test_the_status_reports_a_fault_once_and_the_recovery_once(tmp_path):
    lines = []
    rep = mailbox_status.Reporter(str(tmp_path / "s.json"), clock=lambda: 1000.0,
                                  out=lambda *a, **k: lines.append(a[0]))
    err = mt.MailTransportError("auth", "imap", "mail.corp.example", 993, "LOGIN failed")
    rep.fault(err)
    rep.fault(err)
    assert lines == ["mailbox FAULT mail:auth imap mail.corp.example:993 — LOGIN failed"]
    ready, why = mailbox_status.check(str(tmp_path / "s.json"), now=1001.0)
    assert not ready and why == "mail:auth imap mail.corp.example:993 — LOGIN failed"
    rep.ok("imap")
    assert lines[-1].startswith("mailbox RECOVERED")
    assert mailbox_status.check(str(tmp_path / "s.json"), now=1001.0) == (True, "mailbox ok · inbox imap")


def test_the_probe_is_not_ready_before_a_first_poll_or_when_the_loop_goes_silent(tmp_path):
    p = str(tmp_path / "s.json")
    assert mailbox_status.check(p)[0] is False
    mailbox_status.Reporter(p, clock=lambda: 0.0, out=lambda *a, **k: None).ok("imap")
    ready, why = mailbox_status.check(p, now=mailbox_status.STALE_AFTER_S + 1.0)
    assert not ready and "silent" in why
