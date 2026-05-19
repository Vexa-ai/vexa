# Base Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install schema-sync library
COPY ./libs/schema-sync /app/libs/schema-sync
RUN pip install --no-cache-dir /app/libs/schema-sync

# Install admin models library
COPY ./libs/admin-models /app/libs/admin-models
RUN pip install --no-cache-dir /app/libs/admin-models

# Install meeting-api models (admin reads meeting stats)
COPY ./services/meeting-api /app/services/meeting-api
RUN pip install --no-cache-dir /app/services/meeting-api

# Run as non-root. Create the user before copying app code so COPY can set
# ownership even when the source checkout has restrictive local file modes.
RUN useradd -r -s /bin/false appuser

# Copy application code and requirements
COPY --chown=appuser:appuser ./services/admin-api/requirements.txt /app/
COPY --chown=appuser:appuser ./services/admin-api/app /app/app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

RUN chmod -R a+rX /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/')"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
