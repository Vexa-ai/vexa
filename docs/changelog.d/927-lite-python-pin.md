- **Vexa Lite pins its service interpreters to Python 3.12, so admin-api boots again (#927).** The
  single-container Lite image builds each service venv with `uv venv` on a Playwright/jammy base
  whose only system Python is 3.10 — below `requires-python >=3.11`. An unpinned `uv venv` therefore
  auto-downloaded the newest managed CPython (3.14), on which the frozen async-Postgres stack fails
  (asyncpg's C extension will not build under 3.14; SQLAlchemy's psycopg dialect import crashes at
  startup), so `admin-api` entered a supervisor FATAL loop and API-key provisioning silently no-oped
  — the gateway then answered every request with `Authentication temporarily unavailable`. All five
  Lite venvs (admin-api, runtime, meeting-api, gateway, agent) now pass `--python 3.12`, matching the
  `FROM python:3.12-slim` every compose image (and helm, which ships those images) already pinned.
  Compose and helm never shared the exposure.
