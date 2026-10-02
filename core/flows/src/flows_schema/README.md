# flows_schema

The schema SOURCE OF TRUTH: declarative SQLAlchemy models, house-style (same pattern as
meeting-api collector/sessions, admin-api). `schema.sql` is generated from here
(`scripts/gen_schema.py`); a drift test keeps them identical. Lives OUTSIDE `src/flows/` so the
engine stays stdlib-pure at import.
