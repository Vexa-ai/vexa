FROM python:3.12-slim

WORKDIR /app

# Copy source and install
COPY pyproject.toml README.md ./
COPY runtime_api/ runtime_api/
COPY profiles.example.yaml profiles.yaml
RUN pip install --no-cache-dir . \
    && groupadd -r runtime && useradd -r -g runtime runtime \
    && chown -R runtime:runtime /app

USER runtime

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8090/health').raise_for_status()" || exit 1

CMD ["uvicorn", "runtime_api.main:app", "--host", "0.0.0.0", "--port", "8090"]
