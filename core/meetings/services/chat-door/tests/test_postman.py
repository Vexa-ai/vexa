"""The postman: artifact parsing · MIME shape · the embedded link · a real SMTP send."""
from __future__ import annotations

import email
from email import policy
from pathlib import Path

import pytest

from chat_door.artifact import load_artifact, with_record_link
from chat_door.postman import build_message, send
from chat_door.tokens import TokenSigner, build_magic_link

from smtp_stub import SMTPStub

KEY = b"postman-test-key"

EN_ARTIFACT = """**To:** Dmitry Grankin
**Meeting:** 2025-08-28 · Google Meet · 59m
**record:** 126 · [open the record](#) *(placeholder link)*

---

**You committed to**

- Issue Henry a $100 promo code and close the $20 Upwork contract.
- Connect on LinkedIn properly.

*Reply to change what I track for you.*
"""

RU_ARTIFACT = """**Кому:** Алексей Рогов
**Встреча:** 2026-05-05 · Microsoft Teams · 78 мин
**запись:** 11706 · [открыть запись](#) *(ссылка-заглушка)*

---

**Решено — касается тебя**

Мяч по Agent API на стороне Димы.
"""


@pytest.fixture
def en(tmp_path: Path) -> Path:
    d = tmp_path / "rendered" / "124"
    d.mkdir(parents=True)
    p = d / "dmitry-grankin.md"
    p.write_text(EN_ARTIFACT, "utf-8")
    return p


@pytest.fixture
def ru(tmp_path: Path) -> Path:
    d = tmp_path / "rendered" / "11706"
    d.mkdir(parents=True)
    p = d / "alexey-rogov.md"
    p.write_text(RU_ARTIFACT, "utf-8")
    return p


# -- parsing --------------------------------------------------------------------

def test_record_line_beats_the_directory_name(en: Path):
    """The corpus keys the folder 124 while the record is 126 — the door needs 126."""
    a = load_artifact(en)
    assert a.meeting_id == "126"
    assert a.recipient_name == "Dmitry Grankin"
    assert a.meeting_label == "2025-08-28 · Google Meet · 59m"
    assert a.participant_slug == "dmitry-grankin"


def test_subject_is_the_meeting_plus_what_changed(en: Path):
    assert load_artifact(en).subject == "2025-08-28 · Google Meet · 59m — what changed for you"


def test_non_english_artifact_keeps_its_language(ru: Path):
    a = load_artifact(ru)
    assert a.meeting_id == "11706"
    assert a.recipient_name == "Алексей Рогов"
    assert a.language == "ru"
    assert a.subject.endswith("что изменилось для тебя")


def test_missing_record_line_falls_back_to_the_folder(tmp_path: Path):
    d = tmp_path / "rendered" / "9434"
    d.mkdir(parents=True)
    p = d / "someone.md"
    p.write_text("**To:** Someone\n\nno header beyond this\n", "utf-8")
    assert load_artifact(p).meeting_id == "9434"


def test_placeholder_link_is_rewritten_once(en: Path):
    body, rewrote = with_record_link(EN_ARTIFACT, "http://door.test/door/verify?t=abc")
    assert rewrote
    assert "](http://door.test/door/verify?t=abc)" in body
    assert "](#)" not in body
    assert "placeholder link" not in body
    assert en.exists()


# -- MIME -----------------------------------------------------------------------

def _built(path: Path, *, to="reader@example.test", scope="guest"):
    a = load_artifact(path)
    signer = TokenSigner(KEY)
    token = signer.issue(kind="link", subject=to, meeting_id=a.meeting_id, scope=scope,
                         ttl_seconds=600)
    link = build_magic_link("http://door.test", token)
    return a, link, build_message(a, to_email=to, link=link)


def test_message_is_multipart_alternative_with_both_bodies(en: Path):
    _, link, built = _built(en)
    msg = built.message
    assert msg.get_content_type() == "multipart/alternative"
    plain = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert link in plain
    assert f'href="{link}"' in html
    assert "<li>" in html  # the bullets survived the rendering


def test_headers_name_the_record_and_the_person(en: Path):
    a, _, built = _built(en)
    msg = built.message
    assert msg["Subject"] == a.subject
    assert "reader@example.test" in msg["To"]
    assert "Dmitry Grankin" in msg["To"]
    assert msg["X-Vexa-Record"] == "126"
    assert msg["X-Vexa-Artifact"] == "dmitry-grankin"
    assert msg["Message-ID"]


def test_the_link_is_scoped_to_this_artifacts_record(en: Path):
    _, link, _ = _built(en)
    token = link.split("t=", 1)[1]
    claims = TokenSigner(KEY).verify(token, expect_kind="link")
    assert claims.meeting_id == "126"
    assert claims.subject == "reader@example.test"
    assert claims.scope == "guest"


def test_russian_artifact_survives_serialization(ru: Path):
    a, link, built = _built(ru, to="alexey@example.test")
    raw = built.message.as_string()
    parsed = email.message_from_string(raw, policy=policy.default)
    assert parsed["Subject"] == a.subject
    assert "Алексей Рогов" in parsed["To"]
    assert link in parsed.get_body(preferencelist=("plain",)).get_content()


def test_artifact_without_a_placeholder_still_gets_the_link(tmp_path: Path):
    d = tmp_path / "rendered" / "500"
    d.mkdir(parents=True)
    p = d / "x.md"
    p.write_text("**To:** X\n**Meeting:** today\n**record:** 500\n\nbody\n", "utf-8")
    _, link, built = _built(p)
    assert built.rewrote_link is False
    assert link in built.message.get_body(preferencelist=("plain",)).get_content()


# -- the wire -------------------------------------------------------------------

def test_send_reaches_an_smtp_server(en: Path):
    _, link, built = _built(en)
    with SMTPStub() as stub:
        send(built.message, host=stub.host, port=stub.port)
    assert len(stub.messages) == 1
    captured = stub.messages[0]
    assert "reader@example.test" in captured.rcpt_to[0]
    parsed = email.message_from_string(captured.data, policy=policy.default)
    assert parsed["X-Vexa-Record"] == "126"
    assert link in parsed.get_body(preferencelist=("plain",)).get_content()


def test_cli_dry_run_writes_the_eml_without_sending(en: Path, tmp_path: Path, monkeypatch):
    from chat_door import postman

    monkeypatch.setenv("CHAT_DOOR_SIGNING_KEY", "cli-test-key")
    monkeypatch.setenv("CHAT_DOOR_BASE_URL", "http://door.test")
    out = tmp_path / "out.eml"
    rc = postman.main([
        "--artifact", str(en), "--to", "reader@example.test", "--dry-run", str(out),
    ])
    assert rc == 0
    raw = out.read_text("utf-8")
    assert "http://door.test/door/verify?t=" in raw
    assert "cli-test-key" not in raw  # the key signs; it never travels


def test_cli_does_not_print_the_link_by_default(en: Path, tmp_path: Path, monkeypatch, capsys):
    from chat_door import postman

    monkeypatch.setenv("CHAT_DOOR_SIGNING_KEY", "cli-test-key")
    postman.main(["--artifact", str(en), "--to", "r@example.test",
                  "--dry-run", str(tmp_path / "o.eml")])
    assert "door/verify?t=" not in capsys.readouterr().out
