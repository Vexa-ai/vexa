"""Filesystem and development-SMTP adapters for the post-meeting ports."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from .service import EmailNotice, MeetingArtifact, PostMeetingFault


class WorkspaceArtifactReader:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read(self, path: str) -> MeetingArtifact | None:
        target = (self._root / path).resolve()
        if self._root not in target.parents:
            raise PostMeetingFault(
                source="workspace", kind="invalid-path", detail=f"artifact escapes workspace: {path}",
            )
        try:
            return MeetingArtifact(path=path, content=target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PostMeetingFault(
                source="workspace", kind="read-failed", detail=str(exc),
            ) from exc


class DevSmtpEmailSink:
    """Development-only SMTP sink (normally Mailpit); production delivery remains a separate adapter."""

    def __init__(self, address: str, *, sender: str) -> None:
        host, separator, port = address.partition(":")
        self._host = host
        self._port = int(port if separator else "25")
        self._sender = sender
        self._sent: set[str] = set()

    def send(self, notice: EmailNotice, *, idempotency_key: str) -> None:
        if idempotency_key in self._sent:
            return
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = notice.to
        message["Subject"] = notice.subject
        message["X-Vexa-Idempotency-Key"] = idempotency_key
        message.set_content(notice.body)
        try:
            with smtplib.SMTP(self._host, self._port) as smtp:
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise PostMeetingFault(source="dev-smtp", kind="send-failed", detail=str(exc)) from exc
        self._sent.add(idempotency_key)
