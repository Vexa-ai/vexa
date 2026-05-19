FROM python:3.11-slim

WORKDIR /app

# Run as non-root. Create the user before copying service code so COPY can
# normalize ownership even when the local checkout has restrictive file modes.
RUN useradd -r -s /bin/false appuser

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY --chown=appuser:appuser services/mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy MCP service code
COPY --chown=appuser:appuser services/mcp/ .

RUN chmod -R a+rX /app
USER appuser

EXPOSE 18888

CMD ["python", "main.py"]
