"""admin-api — the v0.12 identity service carve + the Group-1 backing-stack evals.

Public surface:
  - schema.models : the v0.12 SQLAlchemy source-of-truth (identity + meeting tables)
  - schema.sync   : ensure_schema() idempotent convergence
  - app.main      : create_app() FastAPI surface (3 auth tiers, /internal/validate)
  - token_scope   : vxa_<scope>_ token minting
"""
