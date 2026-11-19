"""The flows engine's remaining security rows: the key, the tokens, the urls, the ICS, the compare.

R-B11 · R-B13 · R-B14 · R-B15 · R-B16 from the 2026-09-02 release backlog: the flows key has no
default, no gateway token is minted for a person, `email` and `path` are encoded into internal
urls, the iMIP reply escapes ICS and its `Subject`, and the operator-key comparison is constant
time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import flows_config  # noqa: E402
import flows_steps.common as common  # noqa: E402
import flows_steps.emailx as emailx  # noqa: E402


# ── R-B11 · the flows key has no default, because it is the tier's one way into the platform ──
def test_the_service_key_has_no_default_at_all(monkeypatch):
    """The admin key this row was first written about is gone from flows: the tier holds ONE
    credential, the flows key, and it is refused exactly as the admin key was — unset stops the
    caller, it is never a literal in this file."""
    monkeypatch.delenv("VEXA_FLOWS_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError) as e:
        common.require_service_key()
    assert "VEXA_FLOWS_SERVICE_KEY is unset" in str(e.value)


@pytest.mark.parametrize("weak", ["changeme", "change-me", "default", "secret"])
def test_the_placeholders_are_refused_by_name(monkeypatch, weak):
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", weak)
    with pytest.raises(RuntimeError):
        common.require_service_key()


@pytest.mark.parametrize("published", ["vexa-internal-secret", "lite-internal-secret", "changeme",
                                       "change-me", "CHANGE-ME", "default", "secret"])
def test_every_value_shipped_for_a_secret_is_refused_where_it_is_used(monkeypatch, published):
    """The boot refuses these (config.v1 `forbidden_values`), and so does the use site, from the
    same list (fact placeholder-secrets)."""
    assert published in common.PLACEHOLDER_SECRETS
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", published)
    with pytest.raises(RuntimeError):
        common.require_service_key()
    with pytest.raises(RuntimeError):
        common.door_headers("7")


def test_no_admin_key_is_held_any_more():
    """Flows mints no gateway token and creates no account with an admin key: it names the subject
    through the service door instead. The admin-key surface is GONE, not merely unused."""
    for name in ("ADMIN_KEY", "require_admin_key", "_admin_headers", "ADMIN_KEY_PLACEHOLDERS",
                 "user_api_key", "_KEY_CACHE", "_key_ttl"):
        assert not hasattr(common, name), f"common.{name} is back"
    assert "VEXA_FLOWS_ADMIN_KEY" not in flows_config.DECLARED
    assert "VEXA_FLOWS_ADMIN_KEY" not in flows_config.SECRETS


def test_no_module_constant_carries_the_key():
    """A constant read at import forces the refusal into import time — where a test that never
    touches another domain pays for it and the failure is blamed on whoever imported first."""
    assert not hasattr(common, "SERVICE_KEY")
    assert not hasattr(common, "ADMIN_KEY")


def test_the_refusal_never_prints_the_value(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "changeme")
    with pytest.raises(RuntimeError) as e:
        common.require_service_key()
    assert "changeme" in str(e.value), "the PLACEHOLDER may be named — it is not a secret"
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "s3cr3t-real-value-abcdef")
    assert common.require_service_key() == "s3cr3t-real-value-abcdef"


def test_no_test_in_this_suite_hardcodes_the_old_default():
    """The smallest fix for R-B11 is two edits, and this is the second: *drop the test's hardcoded
    copy*. `test_contract_smokes.py` carried `"changeme"` twice, which is how a default survives
    its own deletion."""
    here = Path(__file__).resolve().parent
    for f in sorted(here.glob("test_*.py")):
        if f.name == Path(__file__).name:
            continue
        text = f.read_text()
        for header in ('"X-Admin-API-Key": "changeme"', "'X-Admin-API-Key': 'changeme'",
                       '"X-Vexa-Service-Key": "changeme"', "'X-Vexa-Service-Key': 'changeme'"):
            assert header not in text, f"{f.name} still presents a removed default as a key"


# ── R-B13 · no token is minted at all ─────────────────────────────────────────────────────────
def _wire(monkeypatch):
    import flows_steps.meeting as mt
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "real-flows-key")
    calls = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append((method, url, dict(headers or {}), body))
        if "/transcripts/" in url:
            return 200, {"segments": [{"speaker": "Anna", "text": "hello"}]}
        return 200, []

    monkeypatch.setattr(common, "http", fake_http)
    monkeypatch.setattr(mt, "http", fake_http)
    monkeypatch.setattr(mt, "meetings_door", lambda: "http://gw")
    return mt, calls


def test_thirty_reads_mint_no_token(monkeypatch):
    """The organiser's account used to collect a `["bot","browser","tx"]` token per gateway read —
    about thirty for one 20-person meeting (R-B13). Now a read names its subject through the
    service door and NOTHING is minted: no request ever reaches a `/tokens` route."""
    mt, calls = _wire(monkeypatch)
    for _ in range(30):
        mt.transcript_text("7", 41)
    assert len(calls) == 30
    assert not [c for c in calls if "/tokens" in c[1] or "/admin/" in c[1]]
    assert all(c[2] == {"X-Vexa-Service-Key": "real-flows-key", "X-User-Id": "7"} for c in calls)


def test_two_users_are_two_subjects_never_one_shared_credential(monkeypatch):
    mt, calls = _wire(monkeypatch)
    mt.transcript_text("7", 41)
    mt.transcript_text("8", 42)
    assert [c[2]["X-User-Id"] for c in calls] == ["7", "8"]
    assert not [c for c in calls if "X-API-Key" in c[2]]


def test_the_ttl_knob_is_gone_with_the_mint():
    """`VEXA_FLOWS_USER_KEY_TTL_S` bounded the lifetime of a minted token. There is no token, so
    there is no knob: a key nothing reads is a surface for no reader (P14 the other way round)."""
    assert "VEXA_FLOWS_USER_KEY_TTL_S" not in flows_config.DECLARED
    src = Path(common.__file__).read_text()
    assert "VEXA_FLOWS_USER_KEY_TTL_S" not in src and "/tokens" not in src


# ── R-B14 · attacker-supplied strings are encoded into internal urls ─────────────────────────
def test_an_address_off_an_ics_line_cannot_re_point_an_internal_request(monkeypatch):
    """`email` comes off the invite's own `ATTENDEE`/`ORGANIZER` line. Unencoded, a `/` or a `?`
    in it addresses a different route on a service that trusts this caller."""
    seen = []
    monkeypatch.setattr(common, "http",
                        lambda m, url, h, body=None, timeout=20: seen.append(url) or (404, {}))
    common.platform_user_id("../../admin/users/1/tokens?x=@evil.test")
    assert "../.." not in seen[0] and "?" not in seen[0].split("/internal/users/by-email/")[1]
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
    # SECRECY IS ORTHOGONAL TO NECESSITY (the contract schema says so): the pair is
    # `capability` under `mailbox`, because a deployment may not mail at all. The password
    # is required WITHIN the capability — `all` mode makes a set address with no password
    # misconfigured — which is the check that matters, and it is still a P14 secret either way.
    assert flows_config.DECLARED["VEXA_MAIL_APP_PASSWORD"][0] == "capability"
    assert flows_config.DECLARED["VEXA_MAIL_ADDR"][0] == "capability"
