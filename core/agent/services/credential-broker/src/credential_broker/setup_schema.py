"""setup_schema.py — the SHAPE of a custom-service setup proposal, and how a refusal names its field.

THIS FILE IS VENDORED, byte for byte: canonical here in the credential broker (which validates and
stores a proposal), copied to agent-api (`control_plane/connection_setup_schema.py`, which publishes
the shape on the agent's `connection_request` tool so an agent sees every allowed key before it
writes one). `gate:fact-parity` compares the copies (`scripts/parity.json`, fact
`connection-setup-schema`). Edit this one and copy it out. Pydantic only.

What a proposal MEANS — a public HTTPS endpoint, Telegram's three endpoints, OAuth's bearer rule —
is the broker's `connection_setup.validate`, not this file. This file is the vocabulary: every key a
proposal may carry, and nothing else (`extra='forbid'` all the way down).
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class InputField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,39}$',
                      description='the parameter name the service expects')
    label: str = Field(min_length=1, max_length=80, description='what the person is asked for')
    location: Literal['query', 'body'] = 'body'


class OAuthSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    authorization_url: str = Field(max_length=2000)
    token_url: str = Field(max_length=2000)
    scopes: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(min_length=1, max_length=30)
    token_auth: Literal['client_secret_post', 'client_secret_basic'] = 'client_secret_post'


class SetupSpec(BaseModel):
    """A custom service's setup, proposed for the person to review. The service's NAME is the
    connection's `label`, never a key here."""
    model_config = ConfigDict(extra='forbid')
    oauth: OAuthSpec | None = None
    documentation_url: str = Field(default='', max_length=2000)
    endpoint: str = Field(default='', max_length=2000, description='the exact public HTTPS endpoint')
    header: str = Field(default='Authorization', max_length=64,
                        description='Authorization or X-API-Key')
    scheme: Literal['bearer', 'raw', 'telegram'] = 'bearer'
    method: Literal['GET', 'POST'] = 'GET'
    secret_label: str = Field(default='API token', min_length=1, max_length=80)
    fields: list[InputField] = Field(default_factory=list, max_length=10)


def describe(error: ValidationError, *, prefix: str = 'setup') -> str:
    """One sentence per rejected field, naming it — never the value that was sent (it may be a
    secret pasted where it should not be)."""
    parts = []
    for e in error.errors(include_url=False, include_input=False):
        where = '.'.join([prefix, *(str(p) for p in e.get('loc') or ())])
        if e.get('type') == 'extra_forbidden':
            parts.append(f'{where} is not a setup field (allowed: '
                         f'{", ".join(allowed_keys(e.get("loc") or ()))})')
        else:
            parts.append(f'{where}: {e.get("msg", "invalid")}')
    return '; '.join(parts) or f'{prefix} is invalid'


def allowed_keys(loc) -> list:
    """The keys allowed beside the rejected one: ``loc`` is the error's location inside a setup."""
    model = SetupSpec
    for part in tuple(loc)[:-1]:
        if isinstance(part, int):
            continue
        field = model.model_fields.get(str(part))
        inner = getattr(field, 'annotation', None)
        for candidate in (OAuthSpec, InputField):
            if inner is candidate or candidate in getattr(inner, '__args__', ()):
                model = candidate
                break
    return list(model.model_fields)
