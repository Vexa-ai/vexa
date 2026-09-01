"""Schema-convergence errors.

Deliberately dependency-free (no SQLAlchemy import) so ``admin_api.__main__`` — which must stay
importable in the offline gate venv where SQLAlchemy/asyncpg are absent — can name this type
without dragging the ORM in.
"""
from __future__ import annotations


class SchemaInvariantError(RuntimeError):
    """A UNIQUE index the application treats as an invariant could not be created.

    Raised out of ``ensure_schema`` and NOT retried: it is a data problem, not a connectivity
    one, so the only cure is operator action on the rows that block the index.
    """
