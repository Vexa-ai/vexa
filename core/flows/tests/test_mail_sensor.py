"""THE MAIL SENSOR — the edge filter on a mailbox other people read too (global mail, 2026-10-14).

  1. An invitation is kept; a reply to mail Vexa sent (In-Reply-To or References = a Message-ID in
     `mail_thread` or the outbound audit `mail_send`) is kept; anything else is DROPPED.
  2. A dropped message emits nothing and leaves NO ROW anywhere (no quarantine, no turn, no seen).
  3. The cursor still moves past a dropped message.
  4. `dry_run` (sensor_test) reports what would be emitted and dropped, by sender DOMAIN only,
     and emits nothing.
  5. `VEXA_MAIL_INBOUND_FILTER=all` keeps the previous behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows_integrations.inbox import InboundMessage  # noqa: E402
from flows_integrations.mail_sensor import MailSensor, classify, sensor_id_for  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

ICS = "BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nUID:x\nEND:VEVENT\nEND:VCALENDAR\n"


def _msg(n, frm="customer@shop.example.org", subject="Order question", headers=None, ics=None):
    return InboundMessage(cursor=str(n), ext_id=str(n), message_id=f"<in-{n}@x>", frm=frm,
                          subject=subject, headers=dict(headers or {}), body="private words",
                          ics=ics)


class _Inbox:
    name = "fake"

    def __init__(self, msgs):
        self.msgs, self.committed = list(msgs), []

    def fetch(self, cursor):
        return [m for m in self.msgs if int(m.cursor) > int(cursor)]

    def commit(self, db, msg):
        self.committed.append(msg.cursor)


def _tables_with_rows(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {n for n in names if db.execute(f"SELECT COUNT(*) FROM {n}")[0][0]}


def test_invites_and_replies_to_vexa_mail_are_kept_everything_else_dropped(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_INBOUND_FILTER", raising=False)
    db = SqliteDB()
    db.execute("INSERT INTO mail_thread (message_id, subject_uid, session, created_at) "
               "VALUES ('<vexa-1@vexa.ai>', '7', 'main', 0)")
    db.execute("INSERT INTO mail_send (id, at, kind, template_version, recipient_domain, result, "
               "message_id, test) VALUES ('a', 0, 'minutes', 'v', 'bank.test', 'sent', "
               "'<vexa-2@vexa.ai>', 0)")
    assert classify(db, _msg(1, ics=ICS))[0] == "invite"
    assert classify(db, _msg(2, headers={"In-Reply-To": "<vexa-1@vexa.ai>"}))[0] == "reply"
    assert classify(db, _msg(3, headers={"References": "<other@x> <vexa-2@vexa.ai>"}))[0] == "reply"
    assert classify(db, _msg(4))[0] is None
    assert classify(db, _msg(5, headers={"In-Reply-To": "<support-thread@shop>"}))[0] is None


def test_a_dropped_message_emits_nothing_and_leaves_no_row_but_the_cursor_moves(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_INBOUND_FILTER", raising=False)
    db = SqliteDB()
    before = _tables_with_rows(db)
    emitted = []
    inbox = _Inbox([_msg(1), _msg(2, frm="someone@else.example.net"), _msg(3, ics=ICS)])
    sensor = MailSensor(sensor_id_for("info@vexa.ai"), "global", inbox, emitted.append)
    cursor = sensor.tick(db, "0")
    assert [m.ext_id for m in emitted] == ["3"]
    assert sensor.dropped == 2
    assert cursor == "3" and inbox.committed == ["1", "2", "3"]
    assert _tables_with_rows(db) == before, "a dropped message must leave no row"


def test_dry_run_reports_domains_only_and_emits_nothing(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_INBOUND_FILTER", raising=False)
    db = SqliteDB()
    emitted = []
    sensor = MailSensor("mail:info@vexa.ai", "global", _Inbox([]), emitted.append)
    out = sensor.dry_run(db, [_msg(1), _msg(2, frm="org@bank.test", ics=ICS)])
    assert emitted == []
    assert out["counts"] == {"emit": 1, "drop": 1}
    assert out["would_emit"][0]["event"] == "invite.received"
    text = repr(out)
    for secret in ("customer@", "Order question", "private words", "org@"):
        assert secret not in text
    assert out["dropped"][0]["from_domain"] == "shop.example.org"


def test_filter_all_keeps_the_previous_behaviour(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_INBOUND_FILTER", "all")
    assert classify(SqliteDB(), _msg(1))[0] == "all"


# ── the workspace an invite is for (founder 2026-10-14: the event body carries its invite link) ──

def test_an_invite_carrying_this_deployments_workspace_link_names_the_workspace(monkeypatch):
    from flows_integrations.mailbox import workspace_invite
    monkeypatch.setenv("VEXA_UI_URL", "https://app.dev.vexa.ai")
    ics = ("BEGIN:VEVENT\r\nDESCRIPTION:Join our workspace: https://app.dev.vexa.ai/jo\r\n in/Tok3n_abc-9\\n"
           "Meet: https://meet.google.com/abc-defg-hij\r\nEND:VEVENT\r\n")
    assert workspace_invite(ics) == {
        "workspace_invite_link": "https://app.dev.vexa.ai/join/Tok3n_abc-9",
        "workspace_invite_token": "Tok3n_abc-9"}


def test_a_link_to_another_deployment_or_none_names_no_workspace(monkeypatch):
    from flows_integrations.mailbox import workspace_invite
    monkeypatch.setenv("VEXA_UI_URL", "https://app.dev.vexa.ai")
    assert workspace_invite("DESCRIPTION:https://evil.example.com/join/Tok3n_abc") == {}
    assert workspace_invite("DESCRIPTION:no link here") == {}
    monkeypatch.delenv("VEXA_UI_URL")
    assert workspace_invite("DESCRIPTION:https://app.dev.vexa.ai/join/Tok3n_abc") == {}
