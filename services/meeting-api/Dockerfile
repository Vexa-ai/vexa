FROM python:3.11-slim

WORKDIR /app

# Install system deps (gcc for building, ffmpeg for audio conversion in deferred transcription)
RUN apt-get update && apt-get install -y --no-install-recommends gcc ffmpeg && rm -rf /var/lib/apt/lists/*

# Install schema-sync library
COPY libs/schema-sync /schema-sync
RUN pip install --no-cache-dir /schema-sync

# Copy admin models first (its plain pydantic dep must not override pydantic[email])
COPY libs/admin-models /admin-models
RUN pip install --no-cache-dir /admin-models

# Install Python deps (includes pydantic[email] which adds email-validator)
COPY services/meeting-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY services/meeting-api .

# Run as non-root
RUN useradd -r -s /bin/false appuser
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]

CMD ["uvicorn", "meeting_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
