FROM python:3.10-slim

WORKDIR /app

# piper-tts bundles espeak-ng data, but we need shared libs for onnxruntime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY services/tts-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/tts-service/main.py .

# Voice models directory — will be auto-populated on first use,
# or mount a volume with pre-downloaded voices for faster startup.
RUN mkdir -p /app/voices
ENV PIPER_VOICES_DIR=/app/voices

# Run as non-root
RUN useradd -r -s /bin/false appuser && chown -R appuser /app/voices
USER appuser

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
