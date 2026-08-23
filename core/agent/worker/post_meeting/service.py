"""Post-commit notification core; no filesystem, SMTP, or Vexa transport dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import quote, urlencode


class PostMeetingFault(RuntimeError):
    def __init__(self, *, source: str, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.source = source
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class MeetingArtifact:
    path: str
    content: str


@dataclass(frozen=True)
class EmailNotice:
    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class MeetingCompletion:
    subject: str
    meeting_id: str
    native_id: str
    platform: str
    title: str
    recipient: str
    commit_sha: str


@dataclass(frozen=True)
class NotificationReceipt:
    idempotency_key: str
    artifact_path: str
    commit_sha: str
    chat_url: str
    summary: str


class ArtifactReader(Protocol):
    def read(self, path: str) -> Optional[MeetingArtifact]: ...


class EmailSink(Protocol):
    def send(self, notice: EmailNotice, *, idempotency_key: str) -> None: ...


def require_personal_workspace(work: Path, *, store_root: Path, subject: str) -> None:
    expected = (store_root / subject).resolve()
    if work.resolve() != expected:
        raise PostMeetingFault(
            source="workspace", kind="not-personal",
            detail=f"post-meeting delivery requires Personal {expected}, got {work.resolve()}",
        )


def require_personal_recipient(recipient: str, *, principal_email: str) -> None:
    """Fail closed unless the email door returns to the owner of this Personal workspace."""
    actual = recipient.strip().casefold()
    expected = principal_email.strip().casefold()
    if not expected or actual != expected:
        raise PostMeetingFault(
            source="identity", kind="recipient-not-principal",
            detail="post-meeting recipient must equal the verified Personal-workspace principal",
        )


def summary_from_markdown(content: str) -> str:
    """Return the first prose block after YAML frontmatter, before structured sections."""
    text = content.strip()
    if text.startswith("---"):
        _, separator, remainder = text[3:].partition("\n---")
        if separator:
            text = remainder.lstrip("\n")
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            break
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()


class PostMeetingNotifier:
    def __init__(self, artifacts: ArtifactReader, email: EmailSink, *, terminal_url: str) -> None:
        self._artifacts = artifacts
        self._email = email
        self._terminal_url = terminal_url.rstrip("/")

    def notify(self, completion: MeetingCompletion) -> NotificationReceipt:
        if not completion.commit_sha.strip():
            raise PostMeetingFault(
                source="workspace", kind="uncommitted",
                detail="post-meeting email requires a workspace commit receipt",
            )
        path = f"kg/entities/meeting/{completion.native_id}.md"
        artifact = self._artifacts.read(path)
        if artifact is None:
            raise PostMeetingFault(
                source="workspace", kind="artifact-missing",
                detail=f"committed meeting artifact is missing: {path}",
            )
        summary = summary_from_markdown(artifact.content)
        if not summary:
            raise PostMeetingFault(
                source="workspace", kind="summary-missing",
                detail=f"committed meeting artifact has no summary: {path}",
            )
        query = urlencode({
            "meeting": f"{completion.platform}/{completion.native_id}",
            "as": completion.recipient,
            "mtitle": completion.title,
        }, quote_via=quote)
        chat_url = f"{self._terminal_url}/?{query}"
        key = (
            f"post-meeting:{completion.subject}:{completion.meeting_id}:"
            f"{completion.commit_sha}"
        )
        self._email.send(EmailNotice(
            to=completion.recipient,
            subject=f"Minutes — {completion.title}",
            body=f"{summary}\n\nAsk anything about this meeting in your Personal workspace:\n{chat_url}\n",
        ), idempotency_key=key)
        return NotificationReceipt(
            idempotency_key=key, artifact_path=artifact.path,
            commit_sha=completion.commit_sha, chat_url=chat_url, summary=summary,
        )
