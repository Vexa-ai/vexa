"""model_providers — the operator's model catalog and the provider port it resolves through.

The front door (P6): import from here, never from a module inside. ``catalog.load()`` at boot,
``Catalog.route(...)`` at dispatch, ``Catalog.listing(...)`` for what a person may pick. See
README.md."""
from control_plane.model_providers.adapters import ADAPTERS
from control_plane.model_providers.catalog import (
    ENV,
    Catalog,
    CatalogError,
    load,
    parse,
    secret_from_env,
)
from control_plane.model_providers.port import (
    CRED_NONE,
    CRED_SECRET,
    CRED_SUBJECT,
    CRED_SUBSCRIPTION,
    CREDENTIAL_MISSING,
    ENDPOINT_REFUSED,
    KINDS,
    NOT_CONFIGURED,
    NOT_PERMITTED,
    SOURCE,
    UNKNOWN_MODEL,
    Capabilities,
    ModelChoiceFault,
    ModelProviderPort,
    ModelRoute,
    RouteContext,
    is_model_id,
)

__all__ = [
    "ADAPTERS", "ENV", "Catalog", "CatalogError", "load", "parse", "secret_from_env",
    "CRED_NONE", "CRED_SECRET", "CRED_SUBJECT", "CRED_SUBSCRIPTION",
    "CREDENTIAL_MISSING", "ENDPOINT_REFUSED", "KINDS", "NOT_CONFIGURED", "NOT_PERMITTED", "SOURCE",
    "UNKNOWN_MODEL", "Capabilities", "ModelChoiceFault", "ModelProviderPort", "ModelRoute",
    "RouteContext", "is_model_id",
]
