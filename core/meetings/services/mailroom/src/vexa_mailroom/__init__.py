"""vexa_mailroom — meetings enter by email invitation. Front door: ``create_app`` · ``Mailroom`` ·
``parse_invite`` (P6: import from the package, never a deep module path)."""
from .app import create_app
from .config import Settings, settings_from_env
from .invite import ParsedMail, Rejection, parse_invite
from .meeting_link import find_meeting_link, parse_meeting_url
from .ports import Binding, MailMessage, Notice
from .service import Mailroom, Outcome, PollResult, normalize_address
from .store import FileStore, MemoryStore

__all__ = [
    "create_app", "Mailroom", "Outcome", "PollResult", "normalize_address",
    "parse_invite", "ParsedMail", "Rejection",
    "parse_meeting_url", "find_meeting_link",
    "Binding", "MailMessage", "Notice",
    "MemoryStore", "FileStore",
    "Settings", "settings_from_env",
]
