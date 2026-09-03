"""The flows engine's remaining security rows: the key, the tokens, the urls, the ICS, the compare.

R-B11 · R-B13 · R-B14 · R-B15 · R-B16 from the 2026-09-02 release backlog. Each test below fails
on `origin/minutes-mcp-viewer` @ b25733d12, where respectively: `ADMIN_KEY` defaulted to
`"changeme"`, `user_api_key` minted a permanent full-scope token on every call, `email` and `path`
were interpolated into internal urls unencoded, the iMIP reply built ICS and its `Subject` by raw
interpolation of the invite's own title, and the operator-key comparison was `!=`.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import flows_config  # noqa: E402
import flows_steps.common as common  # noqa: E402
import flows_steps.emailx as emailx  # noqa: E402


# ── R-B11 · the admin key has no default, because it mints accounts and tokens ────────────────
def test_the_admin_key_has_no_default_at_all(monkeypatch):
    """It sat four lines above `require_internal_secret`, whose docstring says *"a weak default is
    worse than no default… so there is no default"* — and this is the stronger case of the two:
    the key opens `ensure_platform_user` and `user_api_key`, which mints a full-scope gateway
    token for ANY user id the caller names."""
    monkeypatch.delenv("VEXA_FLOWS_ADMIN_KEY", raising=False)
    with pytest.raises(RuntimeError) as e:
        common.require_admin_key()
    assert "VEXA_FLOWS_ADMIN_KEY is unset" in str(e.value)


@pytest.mark.parametrize("weak", ["changeme", "change-me", "default", "secret"])
def test_the_placeholders_are_refused_by_name(monkeypatch, weak):
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", weak)
    with pytest.raises(RuntimeError):
        common.require_admin_key()


def test_no_module_constant_carries_the_key_any_more():
    """A constant read at import forces the refusal into import time — where a test that never
    touches admin-api pays for it and the failure is blamed on whoever imported first."""
    assert not hasattr(common, "ADMIN_KEY")


def test_the_refusal_never_prints_the_value(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", "changeme")
    try:
        common.require_admin_key()
    except RuntimeError as e:
        assert "changeme" in str(e), "the PLACEHOLDER may be named — it is not a secret"
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", "s3cr3t-real-value-abcdef")
    assert common.require_admin_key() == "s3cr3t-real-value-abcdef"


def test_no_test_in_this_suite_hardcodes_the_old_default():
    """The smallest fix for R-B11 is two edits, and this is the second: *drop the test's hardcoded
    copy*. `test_contract_smokes.py` carried `"changeme"` twice, which is how a default survives
    its own deletion."""
    here = Path(__file__).resolve().parent
    for f in sorted(here.glob("test_*.py")):
        if f.name == Path(__file__).name:
            continue
        text = f.read_text()
        for header in ('"X-Admin-API-Key": "changeme"', "'X-Admin-API-Key': 'changeme'"):
            assert header not in text, f"{f.name} still presents the removed default as a key"


# ── R-B13 · one short-lived token, not thirty permanent ones ─────────────────────────────────
def _key_rig(monkeypatch, ttl="900"):
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", "real-key")
    monkeypatch.setenv("VEXA_FLOWS_USER_KEY_TTL_S", ttl)
    common._KEY_CACHE.clear()
    calls = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append((method, url, body))
        return 200, {"token": f"tok-{len(calls)}"}

    monkeypatch.setattr(common, "http", fake_http)
    return calls


def test_one_reaction_mints_one_token_not_one_per_read(monkeypatch):
    """`user_api_key` is called per gateway read — including once per attendee inside
    `mint_transcript_share`. One 20-person meeting left ~30 `["bot","browser","tx"]` tokens on the
    organiser's account, permanently, and nothing ever deleted one."""
    calls = _key_rig(monkeypatch)
    keys = {common.user_api_key("7") for _ in range(30)}
    assert keys == {"tok-1"} and len(calls) == 1


def test_the_token_is_asked_to_expire(monkeypatch):
    """admin-api has taken `expires_in` since the mint endpoint existed. A token a post-meeting
    run needs for four minutes does not need to outlive the deployment."""
    calls = _key_rig(monkeypatch, ttl="600")
    common.user_api_key("7")
    _method, _url, body = calls[0]
    assert body["expires_in"] == 600
    assert body["scopes"] == ["bot", "browser", "tx"]


def test_the_cache_expires_with_the_token(monkeypatch):
    """A cache that outlives the credential it holds is a worse bug than the one it fixes."""
    calls = _key_rig(monkeypatch, ttl="60")
    common.user_api_key("7")
    common._KEY_CACHE["7"] = ("tok-1", 0.0)             # as if the ttl had passed
    assert common.user_api_key("7") == "tok-2"
    assert len(calls) == 2


def test_two_users_never_share_a_key(monkeypatch):
    calls = _key_rig(monkeypatch)
    assert common.user_api_key("7") != common.user_api_key("8")
    assert len(calls) == 2


def test_the_cache_is_process_memory_and_never_a_file():
    """The file's stateless law: everything a step needs travels in refs. A credential cache is a
    cache, not state a step may depend on — a restart simply mints another."""
    src = inspect.getsource(common.user_api_key)
    assert "open(" not in src and "Path(" not in src


# ── R-B14 · attacker-supplied strings are encoded into internal urls ─────────────────────────
def test_an_address_off_an_ics_line_cannot_re_point_an_internal_request(monkeypatch):
    """`email` comes off the invite's own `ATTENDEE`/`ORGANIZER` line. Unencoded, a `/` or a `?`
    in it addresses a different route on a service that trusts this caller."""
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", "real-key")
    seen = []
    monkeypatch.setattr(common, "http",
                        lambda m, url, h, body=None, timeout=20: seen.append(url) or (404, {}))
    common.platform_user_id("../../admin/users/1/tokens?x=@evil.test")
    assert "../.." not in seen[0] and "?" not in seen[0].split("/admin/users/email/")[1]
    assert "%2F" in seen[0] and "%3F" in seen[0]


def test_a_refs_derived_path_cannot_forge_a_second_query_parameter(monkeypatch):
    """One source of `path` is the invite's own `#group:` token."""
    seen = []
    monkeypatch.setattr(common, "http",
                        lambda m, url, h, body=None, timeout=20: seen.append(url) or (404, {}))
    common.ws_file("7", "a.md&slug=_global", slug="mine")
    assert seen[0].count("slug=") == 1
    assert "a.md%26slug%3D_global" in seen[0]


# ── R-B15 · the iMIP reply is a message we sign as ourselves ─────────────────────────────────
def test_a_crafted_title_cannot_inject_calendar_properties(monkeypatch):
    """`\\r\\nATTENDEE;…` in a `SUMMARY` does not corrupt the file — it CLOSES the property and
    opens whichever one the sender names next, inside a REPLY we send from our own mailbox into
    the organizer's calendar."""
    sent = {}
    monkeypatch.setattr(emailx, "creds", lambda **k: ("vexa@acme.test", "pw"))
    monkeypatch.setattr(emailx, "_smtp",
                        lambda: (_FakeSMTP(sent), False))
    emailx.send_rsvp_accept("boss@acme.test", ics_uid="u-1", start_epoch=1_900_000_000.0,
                            title="Standup\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:evil@evil.test")
    ics = sent["ics"]
    lines = ics.split("\r\n")
    attendees = [l for l in lines if l.startswith("ATTENDEE")]
    assert attendees == ["ATTENDEE;PARTSTAT=ACCEPTED;CN=Vexa:mailto:vexa@acme.test"], \
        "the crafted title opened a second ATTENDEE property"
    # the words survive — inside the SUMMARY value, where they belong, escaped
    assert r"SUMMARY:Standup\nATTENDEE\;PARTSTAT=ACCEPTED:mailto:evil@evil.test" in ics


def test_the_subject_header_has_no_line_structure(monkeypatch):
    sent = {}
    monkeypatch.setattr(emailx, "creds", lambda **k: ("vexa@acme.test", "pw"))
    monkeypatch.setattr(emailx, "_smtp", lambda: (_FakeSMTP(sent), False))
    emailx.send_rsvp_accept("boss@acme.test", ics_uid="u-1", start_epoch=1_900_000_000.0,
                            title="Standup\r\nBcc: everyone@acme.test")
    assert "\n" not in sent["subject"] and "\r" not in sent["subject"]
    assert sent["subject"] == "Accepted: Standup Bcc: everyone@acme.test"


@pytest.mark.parametrize("raw,want", [
    ("a;b", r"a\;b"), ("a,b", r"a\,b"), ("a" + chr(92) + "b", r"a\\b"),
    ("a\r\nb", r"a\nb"), ("a\rb", r"a\nb"), ("a\nb", r"a\nb"), (None, "")])
def test_ics_escape_follows_rfc_5545(raw, want):
    assert emailx.ics_escape(raw) == want


def test_the_backslash_is_escaped_first():
    """Otherwise every escape this function adds is escaped again and the value is corrupt."""
    assert emailx.ics_escape(r"a\;b") == r"a\\\;b"


class _FakeSMTP:
    def __init__(self, out):
        self.out = out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, msg):
        self.out["subject"] = msg["Subject"]
        for part in msg.walk():
            if part.get_content_type() == "text/calendar":
                self.out["ics"] = part.get_payload(decode=True).decode()


# ── R-B16 · the operator key is compared in constant time ────────────────────────────────────
def test_the_operator_key_comparison_is_constant_time():
    """This is the key that gates `flows_submit`, which is decision 4's entire access model. `!=`
    returns on the first differing byte, so the time it takes is a function of how much of the key
    the caller already has — which is the whole shape of a byte-at-a-time recovery. agent-api uses
    `hmac.compare_digest` for the equivalent check."""
    src = Path(__file__).resolve().parents[1] / "src/flows_integrations/flows_api.py"
    text = src.read_text()
    assert "hmac.compare_digest" in text
    assert "x_flows_admin_key != API_KEY" not in text
    assert "x_flows_admin_key == API_KEY" not in text


def test_an_empty_expected_key_opens_nothing():
    """`TIMELINE_KEY` is optional. `compare_digest("", "")` is True, so an unset narrow key would
    otherwise be opened by an absent header."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_fa", Path(__file__).resolve().parents[1] / "src/flows_integrations/flows_api.py")
    # the module boots services at import; read the predicate out of its source instead
    text = Path(spec.origin).read_text()
    ns: dict = {"hmac": __import__("hmac")}
    body = text.split("def _same_key(", 1)[1].split("\ndef ", 1)[0]
    exec("def _same_key(" + body, ns)                       # noqa: S102 — the function under test
    assert ns["_same_key"]("", "") is False
    assert ns["_same_key"]("k", "k") is True
    assert ns["_same_key"]("k", "j") is False


# ── PRD decision 18(c) · the mailbox credentials come from the CONTRACT, never from a vault ───

def test_no_product_source_shells_out_to_a_private_vault():
    """`creds()` used to run `sops -d ~/dev/vexa-secrets/business/vexa-mail.enc.env` whenever the
    pair was incomplete: product source decrypting a file inside one developer's home directory on
    one machine. It made the mail path unrunnable for anybody else, invisible to the config
    contract, and silently dependent on a binary nothing installs."""
    text = Path(emailx.__file__).read_text()
    for banned in ("sops", "vexa-secrets", "~/dev", "subprocess"):
        assert banned not in text, f"{banned!r} is back in flows_steps/emailx.py"


def test_an_unnamed_mailbox_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_ADDR", raising=False)
    monkeypatch.delenv("VEXA_MAIL_APP_PASSWORD", raising=False)
    with pytest.raises(flows_config.ConfigError) as e:
        emailx.creds()
    assert "VEXA_MAIL_ADDR" in str(e.value)


def test_a_transport_that_logs_in_refuses_a_half_configured_pair(monkeypatch):
    """The lesson the old docstring already carried: a rig exporting the address and no password
    logged into Gmail as `vexa@storm.test` with the production account's password, and 535 read as
    an expired credential for hours. A half-configured pair is not a configuration."""
    monkeypatch.setenv("VEXA_MAIL_ADDR", "vexa@acme.test")
    monkeypatch.delenv("VEXA_MAIL_APP_PASSWORD", raising=False)
    with pytest.raises(flows_config.ConfigError) as e:
        emailx.creds(login=True)
    assert "VEXA_MAIL_APP_PASSWORD" in str(e.value)


def test_the_mail_double_needs_no_password(monkeypatch):
    """The half a blanket refusal would break: mailpit takes no login, so the dogfood lane names a
    host and a port and no credential at all."""
    monkeypatch.setenv("VEXA_MAIL_ADDR", "vexa@storm.test")
    monkeypatch.delenv("VEXA_MAIL_APP_PASSWORD", raising=False)
    assert emailx.creds(login=False) == ("vexa@storm.test", "")


def test_the_refusal_names_the_key_and_never_the_value(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_ADDR", "vexa@acme.test")
    monkeypatch.setenv("VEXA_MAIL_APP_PASSWORD", "s3cr3t-app-password")
    assert emailx.creds() == ("vexa@acme.test", "s3cr3t-app-password")
    monkeypatch.delenv("VEXA_MAIL_APP_PASSWORD", raising=False)
    try:
        emailx.creds()
    except flows_config.ConfigError as e:
        assert "s3cr3t-app-password" not in str(e)


def test_the_smtp_password_is_declared_a_credential(monkeypatch):
    """P14: a value in `SECRETS` is never logged, never goldened, and is fed from a secret store by
    the deploy surface — not read out of one by the service itself."""
    assert "VEXA_MAIL_APP_PASSWORD" in flows_config.SECRETS
    assert flows_config.DECLARED["VEXA_MAIL_APP_PASSWORD"][0] == "required-explicit"
    assert flows_config.DECLARED["VEXA_MAIL_ADDR"][0] == "required-explicit"
