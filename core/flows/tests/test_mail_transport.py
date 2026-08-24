"""The transport seam — doubles only, no network, no sops.

What is pinned here is the part that must NOT vary between Gmail-IMAP, generic IMAP and Graph:
host configuration, cursor durability, and the RFC822 → InboundMessage shape every downstream
consumer (route, handle, admission) reads."""
from __future__ import annotations

import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import SqliteDB  # noqa: E402
from flows_integrations import mail_transport as mt  # noqa: E402

ENV_KEYS = ["VEXA_MAIL_TRANSPORT", "VEXA_MAIL_IMAP_HOST", "VEXA_MAIL_IMAP_PORT",
            "VEXA_MAIL_IMAP_FOLDER", "VEXA_MAIL_SMTP_HOST", "VEXA_MAIL_SMTP_PORT",
            "VEXA_MAIL_SMTP_STARTTLS"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VEXA_MAIL_ADDR", "info@vexa.ai")          # never shell out to sops
    monkeypatch.setenv("VEXA_MAIL_APP_PASSWORD", "x")
    mt.reset_transport_cache()
    yield
    mt.reset_transport_cache()


# --------------------------------------------------------------------------------- selection
def test_gmail_is_the_default_and_supplies_googles_hosts():
    tp = mt.get_transport()
    assert isinstance(tp, mt.ImapSmtpTransport)
    assert (tp.imap_host, tp.imap_port) == ("imap.gmail.com", 993)
    assert (tp.smtp_host, tp.smtp_port, tp.starttls) == ("smtp.gmail.com", 465, False)
    assert tp.address() == "info@vexa.ai"


def test_generic_imap_takes_its_hosts_from_env_and_refuses_to_guess(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_TRANSPORT", "imap")
    with pytest.raises(ValueError, match="VEXA_MAIL_IMAP_HOST"):
        mt.get_transport(fresh=True)
    # Exchange Online with IMAP enabled — zero new code, four env vars.
    monkeypatch.setenv("VEXA_MAIL_IMAP_HOST", "outlook.office365.com")
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "smtp.office365.com")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PORT", "587")
    monkeypatch.setenv("VEXA_MAIL_SMTP_STARTTLS", "1")
    monkeypatch.setenv("VEXA_MAIL_IMAP_FOLDER", "Inbox")
    tp = mt.get_transport(fresh=True)
    assert (tp.imap_host, tp.imap_port, tp.folder) == ("outlook.office365.com", 993, "Inbox")
    assert (tp.smtp_host, tp.smtp_port, tp.starttls) == ("smtp.office365.com", 587, True)


def test_gmail_hosts_are_defaults_not_hardcodes(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_IMAP_HOST", "imap.on-prem.bank.local")
    assert mt.get_transport(fresh=True).imap_host == "imap.on-prem.bank.local"


def test_unknown_transport_is_a_loud_error(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_TRANSPORT", "carrier-pigeon")
    with pytest.raises(ValueError, match="unknown VEXA_MAIL_TRANSPORT"):
        mt.get_transport()


def test_transport_is_cached_per_process():
    assert mt.get_transport() is mt.get_transport()
    assert mt.get_transport(fresh=True) is not None


# ------------------------------------------------------------------------ cursor durability
def test_cursor_roundtrips_for_both_integer_and_token_shapes():
    db = SqliteDB()
    assert mt.read_cursor(db) is None                     # first boot: nothing written yet
    mt.write_cursor(db, "412")
    assert mt.read_cursor(db) == "412"
    assert db.execute("SELECT uid FROM mail_cursor WHERE id = 1")[0][0] == 412
    mt.write_cursor(db, "2026-08-24T09:00:00Z")           # Graph timestamp position
    assert mt.read_cursor(db) == "2026-08-24T09:00:00Z"
    delta = "https://graph.microsoft.com/v1.0/…/delta?$deltatoken=abc"
    mt.write_cursor(db, delta)
    assert mt.read_cursor(db) == delta
    assert len(db.execute("SELECT id FROM mail_cursor")) == 1, "cursor must stay a singleton row"


def test_a_pre_seam_database_still_polls_and_says_so_when_it_cannot():
    """`schema.sql` is CREATE-IF-NOT-EXISTS with no migration runner: an already-deployed flows
    tier has no `token` column. IMAP must keep working; Graph must fail loudly rather than
    silently rewinding the mailbox to the start."""
    db = SqliteDB()
    db.execute("ALTER TABLE mail_cursor DROP COLUMN token")
    assert mt.has_token_column(db) is False
    mt.write_cursor(db, "77")
    assert mt.read_cursor(db) == "77"
    with pytest.raises(RuntimeError, match="ADD COLUMN token"):
        mt.write_cursor(db, "2026-08-24T09:00:00Z")


# ----------------------------------------------------------------------------- message shape
def exchange_mime() -> bytes:
    m = EmailMessage()
    m["From"] = '"Anna Bank" <Anna.Bank@OeNB.at>'
    m["To"] = "info@vexa.ai"
    m["Subject"] = "Quarterly risk review"
    m["Message-ID"] = "<AM0PR01MB1234567890@AM0PR01MB1234.eurprd01.prod.outlook.com>"
    m["In-Reply-To"] = "<earlier@vexa.ai>"
    m.set_content("Sehr geehrte Damen und Herren,\n> quoted\nsee attached")
    ics = Path(__file__).resolve().parent.joinpath("fixtures_exchange/outlook-w-europe.ics").read_bytes()
    m.add_attachment(ics, maintype="text", subtype="calendar", filename="invite.ics",
                     params={"method": "REQUEST"})
    return m.as_bytes()


def test_rfc822_becomes_the_transport_independent_message():
    msg = mt._from_rfc822(exchange_mime(), "412")
    assert msg.cursor == "412"
    assert msg.frm == "anna.bank@oenb.at", "address must be lowercased for routing"
    assert msg.message_id.startswith("<AM0PR01MB")
    assert msg.headers["In-Reply-To"] == "<earlier@vexa.ai>"
    assert "see attached" in msg.body
    assert msg.ics is not None and "BEGIN:VEVENT" in msg.ics


def test_ics_is_found_by_filename_when_the_content_type_is_generic():
    raw = exchange_mime().replace(b"text/calendar", b"application/octet-stream")
    assert mt._from_rfc822(raw, "1").ics is not None


def test_imap_fetch_ignores_the_phantom_last_uid(monkeypatch):
    """`UID n:*` returns the newest UID even when nothing is newer than n — polling on that
    without the guard re-admits the same mail forever."""
    class FakeIMAP:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def uid(self, cmd, *args):
            if cmd == "search":
                return "OK", [b"412"]                     # the phantom: 412 is the cursor itself
            raise AssertionError("must not fetch a message it already has")

    tp = mt.get_transport()
    monkeypatch.setattr(tp, "_imap", lambda: FakeIMAP())
    assert list(tp.fetch("412")) == []
