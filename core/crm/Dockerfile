FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
RUN pip install --no-cache-dir uv==0.11.33
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PYTHONPATH=/app/src PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY mcp.tools.v1.json ./
# Runs as the stack service uid; the Helm pod already asks for it (vexa.nonRootPodSecurityContext).
RUN groupadd --system --gid 10001 vexa \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin vexa
USER 10001:10001
EXPOSE 8300
CMD ["python", "-m", "crm"]
