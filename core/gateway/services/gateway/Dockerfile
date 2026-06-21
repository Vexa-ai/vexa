# syntax=docker/dockerfile:1
# Gateway (v0.12 P4) — the production edge: auth · routing · /ws fan-out.
# Build context: gateway/services/gateway (pyproject.toml + uv.lock + src/).
FROM python:3.12-slim

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# 1) Resolve + install the pinned dependency set from the lockfile (no project — package=false).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 2) Production-only runtime deps NOT in the (lean) test pyproject: the ASGI server + the real
#    redis fan-in client (httpx is already pinned). Kept out of pyproject so the offline gate venv
#    stays minimal; pinned here for a deterministic image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0" "redis==5.2.1"

# 3) The shipped source (src/ is on PYTHONPATH; the package is not pip-installed).
COPY src ./src

EXPOSE 8000
# python -m gateway → uvicorn gateway.adapters:app (HOST/PORT from env).
CMD ["python", "-m", "gateway"]
