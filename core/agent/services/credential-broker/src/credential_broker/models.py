"""The request bodies every broker route accepts (credential-broker.v1).

One pydantic model per contract request shape, each `extra="forbid"`: a field the contract does not
name is refused before a route runs. The routes import these; `tests/test_contract_conformance.py`
holds them to the contract's request goldens.
"""
from __future__ import annotations

import base64
import binascii
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROVIDER_PATTERN = r"^(custom_secret|google_calendar|google_email)$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupBody(Strict):
    label: str = Field(default="Connection", min_length=1, max_length=80)
    provider: str = Field(pattern=PROVIDER_PATTERN)


class CustomSecretBody(Strict):
    setup_request: str = Field(default="", max_length=80)
    confirmed_host: str = Field(default="", max_length=253)
    fields: dict[str, str] = Field(default_factory=dict, max_length=10)
    value: str = Field(default="", max_length=65536)
    endpoint: str = Field(default="", max_length=2000)
    header: str = Field(default="Authorization", max_length=64)
    scheme: Literal["bearer", "raw"] = "bearer"
    method: Literal["GET", "POST"] = "GET"


class OAuthApplicationBody(Strict):
    client_id: str = Field(min_length=1, max_length=200)
    client_secret: str = Field(min_length=1, max_length=2000)
    setup_request: str = Field(max_length=80)
    confirmed_host: str = Field(default="", max_length=253)
    #: The other credential hosts the person typed: the service endpoint, when it is not the token host.
    confirmed_hosts: list[Annotated[str, Field(max_length=253)]] = Field(default_factory=list, max_length=4)


class PreparedSetupBody(Strict):
    setup: dict


#: Domains and calendar ids as the policy lists them.
Domain = Annotated[str, Field(min_length=3, max_length=253, pattern=r"^[a-z0-9.-]+\.[a-z0-9-]+$")]
CalendarId = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\s/?#]+$")]
Email = Annotated[str, Field(min_length=3, max_length=320)]
REQUEST_ID = r"^[a-zA-Z0-9-]{8,80}$"
GOOGLE_ID = r"^[a-zA-Z0-9_-]{1,128}$"
#: Google Calendar event ids: base32hex characters, 5 to 1024 of them (plus `_` for instances).
EVENT_ID = r"^[a-v0-9_]{5,1024}$"


class ActionPolicy(Strict):
    """The administrator's policy for this action, narrowed by the person, as agent-api resolved it
    from `_global/POLICIES.md`. The broker enforces it again; absent, the safe defaults below apply.
    A list that is null means any."""
    unattended: bool = False
    notify_attendees: bool = False
    attendee_domains: Optional[list[Domain]] = Field(default=None, max_length=100)
    calendars: Optional[list[CalendarId]] = Field(default_factory=lambda: ["primary"], max_length=100)
    recipient_domains: Optional[list[Domain]] = Field(default=None, max_length=100)
    attachments: bool = False
    attachment_max_bytes: int = Field(default=0, ge=0, le=20480 * 1024)


class ActionPreferences(Strict):
    """A person's own, narrower setting for the connection policy keys. Absent = as the
    administrator allows. agent-api refuses one that would widen beyond the administrator's
    policy; the broker stores it per person."""
    connection_actions: Optional[list[Literal[
        "gmail.search", "gmail.read", "gmail.thread", "calendar.events", "gmail.draft", "gmail.draft.update",
        "calendar.event.create", "calendar.event.update", "calendar.event.cancel"]]] = None
    connection_actions_unattended: Optional[list[Literal[
        "gmail.search", "gmail.read", "gmail.thread", "calendar.events", "gmail.draft", "gmail.draft.update",
        "calendar.event.create", "calendar.event.update", "calendar.event.cancel"]]] = None
    calendar_notify_attendees: Optional[bool] = None
    calendar_attendee_domains: Optional[Union[Literal["any"], list[Domain]]] = None
    calendar_ids: Optional[Union[Literal["any"], list[CalendarId]]] = None
    draft_recipient_domains: Optional[Union[Literal["any"], list[Domain]]] = None
    draft_attachments: Optional[bool] = None
    draft_attachment_max_kb: Optional[int] = Field(default=None, ge=0, le=20480)


class AuthorizeBody(Strict):
    """Which additional permission this consent asks for. Empty: the connection's base scopes."""
    grant: Literal["", "calendar_write"] = ""


class AccountReadBody(Strict):
    action: Literal["gmail.search", "gmail.read", "gmail.thread", "calendar.events"]
    query: str = Field(default="", max_length=500)
    page_token: str = Field(default="", max_length=2048)
    message_id: str = Field(default="", max_length=128)
    time_min: str = Field(default="", max_length=40)
    time_max: str = Field(default="", max_length=40)
    limit: int = Field(default=10, ge=1, le=20)
    policy: Optional[ActionPolicy] = None


class Attachment(Strict):
    """A workspace file agent-api read for the person, carried as base64."""
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00-\x1f\x7f]+$")
    content_type: Literal["application/pdf", "image/png", "image/jpeg", "text/plain", "text/csv", "text/markdown"]
    data: str = Field(min_length=1, max_length=28_000_000)


class GmailDraftBody(Strict):
    request_id: str = Field(pattern=REQUEST_ID)
    recipient: Email
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=50000)
    cc: list[Email] = Field(default_factory=list, max_length=20)
    bcc: list[Email] = Field(default_factory=list, max_length=20)
    #: Reply in a thread: the thread from a read, and optionally the message replied to (default: the
    #: thread's last message). The broker sets In-Reply-To, References and the "Re:" subject.
    thread_id: str = Field(default="", pattern=r"^(|[a-zA-Z0-9_-]{1,128})$")
    in_reply_to: str = Field(default="", pattern=r"^(|[a-zA-Z0-9_-]{1,128})$")
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    policy: Optional[ActionPolicy] = None


class GmailDraftUpdateBody(GmailDraftBody):
    """Replace the content of a draft this broker created for this person."""
    draft_id: str = Field(pattern=GOOGLE_ID)


class CalendarTime(Strict):
    """A timed instant (`date_time`, ISO 8601; `time_zone` required when it carries no offset) or an
    all-day `date` (YYYY-MM-DD)."""
    date_time: str = Field(default="", max_length=40)
    date: str = Field(default="", pattern=r"^(|\d{4}-\d{2}-\d{2})$")
    time_zone: str = Field(default="", max_length=64)


class CalendarEventBody(Strict):
    request_id: str = Field(pattern=REQUEST_ID)
    action: Literal["calendar.event.create", "calendar.event.update", "calendar.event.cancel"]
    calendar_id: CalendarId = "primary"
    #: update and cancel: an event id from a calendar read. Create takes none.
    event_id: str = Field(default="", pattern=r"^(|[a-v0-9_]{5,1024})$")
    title: Optional[str] = Field(default=None, max_length=1024)
    start: Optional[CalendarTime] = None
    end: Optional[CalendarTime] = None
    description: Optional[str] = Field(default=None, max_length=8000)
    location: Optional[str] = Field(default=None, max_length=1024)
    attendees: Optional[list[Email]] = Field(default=None, max_length=100)
    #: Add a Google Meet link (create, or update of an event that has none).
    conference: bool = False
    #: Ask Google to email the attendees. Never the default; refused unless policy allows it.
    notify: bool = False
    policy: Optional[ActionPolicy] = None


class CustomCallBody(Strict):
    parameters: dict[str, str] = Field(default_factory=dict)
    body: Optional[dict] = None


class GitSecretBody(Strict):
    name: str = Field(pattern=r"^(pat/[A-Za-z0-9_.-]{1,128}(/[a-z0-9][a-z0-9.-]{0,63})?|deploy/(user|ws)-[A-Za-z0-9_.-]{1,120}\.(priv|pub))$")
    action: Literal["get", "put", "migrate"]
    value: Optional[str] = Field(default=None, max_length=32768)


BOT_ACCOUNT_ID = r"^[a-f0-9]{32}$"
BOT_ACCOUNT_OWNER = r"^(user:[0-9]{1,20}|company)$"


class BotAccountFile(Strict):
    """One session-profile.v1 file, its bytes base64. A path is relative and never climbs out."""
    path: str = Field(min_length=1, max_length=512)
    data: str = Field(max_length=33554432)

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or "\0" in value or ".." in value.split("/"):
            raise ValueError("profile path must be relative")
        return value

    @field_validator("data")
    @classmethod
    def _base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("profile data must be base64") from None
        return value


class BotAccountSpec(Strict):
    owner: str = Field(pattern=BOT_ACCOUNT_OWNER)
    platform: Literal["google", "microsoft", "zoom"]
    display_name: str = Field(min_length=1, max_length=120)
    created_by: str = Field(pattern=r"^user:[0-9]{1,20}$")


class BotAccountBody(Strict):
    action: Literal["list", "get", "put", "state", "delete"]
    id: Optional[str] = Field(default=None, pattern=BOT_ACCOUNT_ID)
    owners: Optional[list[Annotated[str, Field(pattern=BOT_ACCOUNT_OWNER)]]] = Field(default=None, max_length=64)
    account: Optional[BotAccountSpec] = None
    files: Optional[list[BotAccountFile]] = Field(default=None, max_length=128)
    state: Optional[Literal["ok", "signed_out"]] = None

    @model_validator(mode="after")
    def _fields_for_action(self):
        if self.action == "list":
            if self.owners is None:
                raise ValueError("list needs owners")
        elif self.id is None:
            raise ValueError("this action needs an id")
        if self.action == "put" and (self.account is None or self.files is None):
            raise ValueError("put needs account and files")
        if self.action == "state" and self.state is None:
            raise ValueError("state needs state")
        return self


class CalendarChangesBody(Strict):
    """credential-broker.v1 `CalendarChangesRequest`: whose calendar, which connection, since when."""
    owner: str = Field(min_length=1, max_length=64)
    connection_id: Optional[str] = Field(default=None, max_length=64)
    cursor: Optional[str] = Field(default=None, max_length=4096)


class SignalsModelKeyBody(Strict):
    """credential-broker.v1 `SignalsModelKeyRequest`: which signal-model key the policy names."""
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
