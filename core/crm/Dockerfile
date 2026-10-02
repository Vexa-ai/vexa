FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.9.22
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PYTHONPATH=/app/src PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY mcp.tools.v1.json ./
EXPOSE 8300
CMD ["python", "-m", "crm"]
