# syntax=docker/dockerfile:1
# credential-broker — the Connections credential broker (credential-broker.v1, ADR-0040).
# Build context: this directory (pyproject.toml + uv.lock + src/). The signer it verifies with is
# vendored into src/ (gate:fact-parity holds it to the contract's canonical copy), so nothing
# outside this directory enters the image.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock ./
# --no-dev: pytest and jsonschema are host-side test tooling, not runtime dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src

# An unprivileged user owns the one writable path. The deploy surfaces mount the state volume
# there and the role and store keys read-only from a secret; the root filesystem can be read-only.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin broker \
 && mkdir -p /state && chown 10001:10001 /state && chmod 0700 /state
USER 10001
VOLUME ["/state"]

EXPOSE 8100
# --no-access-log: the OAuth callback's query string carries the consent code and state.
CMD ["uvicorn", "credential_broker.main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
