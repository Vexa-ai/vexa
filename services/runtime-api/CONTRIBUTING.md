# Contributing to Runtime API

Thank you for your interest in contributing!

## Development

```bash
# Clone and install
git clone https://github.com/vexa-ai/runtime-api.git
cd runtime-api
pip install -e ".[dev]"

# Run unit tests
pytest tests/ -v --ignore=tests/test_integration.py

# Run integration tests (requires Docker)
make up
make test-integration
make down

# Lint
ruff check runtime_api/ tests/
```

## Pull Requests

> **Note:** If you're reading this on the standalone `runtime-api` repo, it's a read-only mirror.
> Please submit pull requests to the main monorepo: [vexa-ai/vexa](https://github.com/vexa-ai/vexa).

1. Fork the repo and create a feature branch
2. Write tests for your changes
3. Ensure `pytest` and `ruff check` pass
4. Submit a PR with a clear description

## Reporting Issues

Open an issue on GitHub. Include:
- What you expected vs what happened
- Steps to reproduce
- Runtime API version and backend (Docker/K8s/Process)

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
