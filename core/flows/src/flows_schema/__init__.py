"""flows_schema — the schema SSOT (declarative SQLAlchemy, house-style). OUTSIDE the
stdlib-pure engine package by design: only tooling and the postgres composition import this."""
from .models import Base  # noqa: F401
