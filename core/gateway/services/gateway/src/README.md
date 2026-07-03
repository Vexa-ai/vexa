# gateway/src — the production gateway package source

Holds the `gateway` Python package: the v0.12 carve of `services/api-gateway/main.py` as an
injectable FastAPI app. See `gateway/` for the module breakdown (ports · app · adapters · obs).
`pyproject.toml` puts `src/` on the path (`pythonpath = ["src", "tests"]`).
