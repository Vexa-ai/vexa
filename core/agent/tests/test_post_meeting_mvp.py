"""Fixed meeting-end fixture → agent processing → personal commit → email + chat door."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from llm import CompletionResult
from llm.claude_code import ClaudeCodeHarness
from worker import worker
from worker.post_meeting import (
    DevSmtpEmailSink,
    EmailNotice,
    MeetingCompletion,
    PostMeetingFault,
    PostMeetingNotifier,
    WorkspaceArtifactReader,
    parse_dev_notification_config,
    require_personal_recipient,
    require_personal_workspace,
)


FIXTURE = Path(__file__).resolve().parents[1] / "eval" / "replay" / "gamestop-allin.jsonl"


class FixtureStream:
    def __init__(self, inbox):
        self.inbox = list(inbox)
        self.out: list[tuple[str, dict]] = []

    def xread(self, streams, count=1, block=None):
        if not self.inbox:
            return []
        topic = next(iter(streams))
        entry = self.inbox.pop(0)
        return [(topic, [entry])]

    def xadd(self, topic, fields):
        self.out.append((topic, fields))
        return f"{len(self.out)}-0"


class RecordedCompletion:
    name = "fixture"

    def complete(self, prompt, *, system=None, model=None):
        return CompletionResult(text=json.dumps({
            "notes": [],
            "cards": [
                {"kind": "person", "title": "Ryan Cohen", "body": "GameStop chairman"},
                {"kind": "company", "title": "GameStop", "body": "Company discussed"},
                {"kind": "company", "title": "AppLovin", "body": "Bootstrapped ad platform"},
            ],
        }), model="fixture-model")


def _segments(limit: int = 4) -> list[dict]:
    result = []
    for index, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines()[:limit]):
        item = json.loads(line)
        result.append({
            "segment_id": f"fixture-{index}", "speaker": item["speaker"],
            "text": item["text"], "start": item["start"], "completed": True,
        })
    return result


def test_fixture_meeting_end_commits_summary_then_emails_personal_chat_door(tmp_path):
    personal = tmp_path / "personal-u-42"
    native = "fixture-allin-001"
    delivered_messages = []
    email = DevSmtpEmailSink("mailpit:1025", sender="minutes@dev.vexa.ai")
    notifier = PostMeetingNotifier(
        WorkspaceArtifactReader(personal), email, terminal_url="http://minutes.test",
    )
    delivered = {}

    def fake_exec(argv, cwd):
        doc = Path(cwd) / "kg" / "entities" / "meeting" / f"{native}.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "---\n"
            "type: meeting\n"
            f"id: {native}\n"
            "title: GameStop All-In\n"
            f"meeting_id: {native}\n"
            "session_uid: fixture-session\n"
            "platform: google_meet\n"
            "date: 2026-08-23\n"
            "---\n\n"
            "Ryan Cohen discussed operational discipline at GameStop and the group cited "
            "AppLovin as a bootstrapped success.\n\n"
            "## Attendees\n- [[Ryan Cohen]]\n\n"
            "## Companies\n- [[GameStop]]\n- [[AppLovin]]\n",
            encoding="utf-8",
        )
        yield json.dumps({
            "type": "result", "subtype": "success", "result": "meeting summary written",
            "session_id": "fixture-doc-turn",
        })

    def card_turn(segments):
        yield from worker.meeting_card_turn(
            personal, segments, completion=RecordedCompletion(),
            card_kinds=("person", "company", "product"),
        )

    def doc_turn(cards):
        yield from worker.meeting_doc_turn(
            personal, cards, native=native, meeting_id=native,
            session_uid="fixture-session", platform="google_meet",
            date="2026-08-23", title="GameStop All-In",
        )

    def after_commit(commit):
        completion = MeetingCompletion(
            subject="u-42", meeting_id="meeting-row-42", native_id=native,
            platform="google_meet", title="GameStop All-In",
            recipient="organizer@example.com", commit_sha=commit["commit_sha"],
        )
        delivered["receipt"] = notifier.notify(completion)
        # A duplicate callback in the same dev worker is suppressed by its commit-derived key.
        notifier.notify(completion)

    class FakeSmtp:
        def __init__(self, host, port):
            assert (host, port) == ("mailpit", 1025)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def send_message(self, message):
            delivered_messages.append(message)

    stream = FixtureStream([
        ("1-0", {"payload": json.dumps({"segments": _segments()})}),
        ("2-0", {"payload": json.dumps({"type": "session_end"})}),
    ])
    with mock.patch.object(
        worker, "harness_factory", lambda: ClaudeCodeHarness(exec_fn=fake_exec),
    ), mock.patch("worker.post_meeting.adapters.smtplib.SMTP", FakeSmtp):
        worker.serve_meeting(
            stream, transcript_stream="tc:meeting:meeting-row-42", out_topic="unit:fixture:out",
            card_turn=card_turn, doc_turn=doc_turn, on_doc_committed=after_commit,
            idle_ms=1,
        )

    receipt = delivered["receipt"]
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=personal, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip() == receipt.commit_sha
    assert receipt.artifact_path == f"kg/entities/meeting/{native}.md"
    assert "operational discipline" in receipt.summary
    assert len(delivered_messages) == 1
    [notice] = delivered_messages
    assert notice["To"] == "organizer@example.com"
    assert notice["Subject"] == "Minutes — GameStop All-In"
    assert receipt.summary in notice.get_content()
    assert "Ask anything about this meeting in your Personal workspace" in notice.get_content()
    assert (
        "http://minutes.test/?meeting=google_meet%2Ffixture-allin-001"
        "&as=organizer%40example.com&mtitle=GameStop%20All-In"
    ) in notice.get_content()


def test_development_email_configuration_is_one_strict_validated_value():
    parsed = parse_dev_notification_config(json.dumps({
        "recipient": "organizer@example.com",
        "terminal_url": "http://localhost:3010",
        "smtp": "localhost:1025",
        "sender": "minutes@dev.vexa.ai",
    }))
    assert parsed.smtp == "localhost:1025"

    with pytest.raises(PostMeetingFault) as failure:
        parse_dev_notification_config("not-json")
    assert failure.value.source == "config"
    assert failure.value.kind == "invalid"


def test_post_meeting_delivery_fails_closed_outside_personal(tmp_path):
    personal = tmp_path / "u-42"
    personal.mkdir()
    require_personal_workspace(personal, store_root=tmp_path, subject="u-42")
    with pytest.raises(PostMeetingFault) as failure:
        require_personal_workspace(tmp_path / "shared", store_root=tmp_path, subject="u-42")
    assert failure.value.kind == "not-personal"


def test_post_meeting_recipient_must_be_the_personal_workspace_principal():
    require_personal_recipient(
        "Organizer@Example.com", principal_email="organizer@example.com",
    )
    with pytest.raises(PostMeetingFault) as failure:
        require_personal_recipient(
            "attendee@example.com", principal_email="organizer@example.com",
        )
    assert failure.value.source == "identity"
    assert failure.value.kind == "recipient-not-principal"


def test_dev_smtp_adapter_delivers_to_the_email_double_once():
    delivered = []

    class FakeSmtp:
        def __init__(self, host, port):
            assert (host, port) == ("mailpit", 1025)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def send_message(self, message):
            delivered.append(message)

    sink = DevSmtpEmailSink("mailpit:1025", sender="minutes@dev.vexa.ai")
    notice = EmailNotice(to="organizer@example.com", subject="Minutes — Fixture", body="ready")
    with mock.patch("worker.post_meeting.adapters.smtplib.SMTP", FakeSmtp):
        sink.send(notice, idempotency_key="meeting:42:commit")
        sink.send(notice, idempotency_key="meeting:42:commit")

    assert len(delivered) == 1
    assert delivered[0]["To"] == "organizer@example.com"
    assert delivered[0]["X-Vexa-Idempotency-Key"] == "meeting:42:commit"
