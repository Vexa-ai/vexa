"""Public surface for post-commit meeting notification."""

from .adapters import DevSmtpEmailSink, WorkspaceArtifactReader
from .config import DevNotificationConfig, parse_dev_notification_config
from .service import (
    ArtifactReader,
    EmailNotice,
    EmailSink,
    MeetingArtifact,
    MeetingCompletion,
    NotificationReceipt,
    PostMeetingFault,
    PostMeetingNotifier,
    require_personal_recipient,
    require_personal_workspace,
    summary_from_markdown,
)

__all__ = [
    "ArtifactReader", "DevNotificationConfig", "DevSmtpEmailSink", "EmailNotice", "EmailSink",
    "MeetingArtifact", "MeetingCompletion", "NotificationReceipt", "PostMeetingFault",
    "PostMeetingNotifier", "WorkspaceArtifactReader", "parse_dev_notification_config",
    "require_personal_recipient", "require_personal_workspace", "summary_from_markdown",
]
