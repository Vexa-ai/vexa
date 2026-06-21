"""identity_core — the identity domain CORE (tokens · access · secrets).

Pure, dependency-light authN/authZ primitives, schema-agnostic at the seam and conformant to the
`identity.v1` contract. Distinct from `services/admin-api/` (the runnable carve that owns the DB,
users, and `/internal/validate`); this is the reusable policy + broker logic those services lean on.

Public surface:
- `ScopedToken`, `mint_token`, `validate_token`, `TokenError` (tokens.py)
- `Resource`, `Action`, `AccessDecision`, `AccessPolicy`, `OwnerOnlyPolicy`, `can_access` (access.py)
- `SecretsPort`, `BrokeredSecret`, `PassthroughSecretsBroker` (secrets.py)
"""

from identity_core.access import (
    Action,
    AccessDecision,
    AccessPolicy,
    OwnerOnlyPolicy,
    Resource,
    ResourceKind,
    can_access,
)
from identity_core.secrets import (
    AuditEvent,
    BrokeredSecret,
    PassthroughSecretsBroker,
    SecretsPort,
)
from identity_core.tokens import (
    SCOPES,
    ScopedToken,
    TokenError,
    mint_token,
    validate_token,
)

__all__ = [
    # tokens
    "ScopedToken",
    "TokenError",
    "mint_token",
    "validate_token",
    "SCOPES",
    # access
    "Action",
    "AccessDecision",
    "AccessPolicy",
    "OwnerOnlyPolicy",
    "Resource",
    "ResourceKind",
    "can_access",
    # secrets
    "SecretsPort",
    "BrokeredSecret",
    "PassthroughSecretsBroker",
    "AuditEvent",
]
