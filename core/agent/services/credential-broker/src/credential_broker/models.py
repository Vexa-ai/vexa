"""The request bodies every broker route accepts (credential-broker.v1).

One pydantic model per contract request shape, each `extra="forbid"`: a field the contract does not
name is refused before a route runs. The routes import these; `tests/test_contract_conformance.py`
holds them to the contract's request goldens.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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


class AccountReadBody(Strict):
    action: Literal["gmail.search", "gmail.read", "gmail.thread", "calendar.events"]
    query: str = Field(default="", max_length=500)
    page_token: str = Field(default="", max_length=2048)
    message_id: str = Field(default="", max_length=128)
    time_min: str = Field(default="", max_length=40)
    time_max: str = Field(default="", max_length=40)
    limit: int = Field(default=10, ge=1, le=20)


class GmailDraftBody(Strict):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{8,80}$")
    recipient: str = Field(min_length=3, max_length=320)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=50000)


class CustomCallBody(Strict):
    parameters: dict[str, str] = Field(default_factory=dict)
    body: Optional[dict] = None


class GitSecretBody(Strict):
    name: str = Field(pattern=r"^(pat/[A-Za-z0-9_.-]{1,128}|deploy/(user|ws)-[A-Za-z0-9_.-]{1,120}\.(priv|pub))$")
    action: Literal["get", "put", "migrate"]
    value: Optional[str] = Field(default=None, max_length=32768)
