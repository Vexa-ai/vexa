"""The one reader of secret material, kept in its own module so the import is visible.

A workspace secret has to be usable — a BYOT key that nothing can read is not a key. But "usable"
means one caller at one moment: the code that builds the LLM call for a dispatch. It does not mean
a read endpoint, and the difference between those two is the whole of the leak this design is
built against.

So the reader lives here, alone. ``secrets.py`` (the management surface) does not import it and
``api.py`` (the HTTP surface) does not import it, which makes "no endpoint returns a secret" a
fact about the import graph that a test can assert, rather than a promise about serializers.

The value comes back wrapped. :class:`Material` keeps it off every repr, str and format — the
three ways a value ends up in a log without anyone deciding to put it there — and hands it over
only through ``reveal()``, which is one grep away from an audit of every use. Same shape as
``identity_core.secrets.BrokeredSecret``, which solves this problem for deployment secrets; this
is its workspace-scoped counterpart, and the two should become one port when a service holds both.
"""

from __future__ import annotations

from .errors import NotFound
from .store import ContextStackStore

REDACTED = "***REDACTED***"


class Material:
    """A secret value that will not print itself."""

    __slots__ = ("_value", "workspace_id", "name", "purpose")

    def __init__(self, value: str, *, workspace_id: str, name: str, purpose: str) -> None:
        self._value = value
        self.workspace_id = workspace_id
        self.name = name
        self.purpose = purpose

    def reveal(self) -> str:
        """The value. Every call site is meant to be findable by grepping for this name."""
        return self._value

    def __repr__(self) -> str:
        return f"<Material {self.workspace_id}/{self.name} {REDACTED}>"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return REDACTED


async def use_material(
    store: ContextStackStore, *, workspace_id: str, name: str, purpose: str
) -> Material:
    """Read a workspace secret for use at call time. Not reachable from any route.

    ``purpose`` is required so that the reason is recorded at the call site rather than
    reconstructed from a stack trace later; it is what an access-rights audit reads.
    """
    row = await store.secret_row(workspace_id=workspace_id, name=name)
    if row is None:
        raise NotFound(f"no secret {name!r} on workspace {workspace_id!r}")
    return Material(row.material, workspace_id=workspace_id, name=name, purpose=purpose)


__all__ = ["Material", "use_material", "REDACTED"]
