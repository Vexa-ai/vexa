# src — mailroom service source root

Holds the `vexa_mailroom` Python package (the `pythonpath` for tests, per `pyproject.toml`).
The package is the front door; import `create_app` / `Mailroom` / `parse_invite` from
`vexa_mailroom`, never a deep module path (P6).
