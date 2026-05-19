FROM python:3.10-slim

WORKDIR /app

# Run as non-root. Create the user before copying service code so COPY can
# normalize ownership even when the local checkout has restrictive file modes.
RUN useradd -r -s /bin/false appuser

# piper-tts bundles espeak-ng data, but we need shared libs for onnxruntime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY --chown=appuser:appuser services/tts-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser services/tts-service/main.py .

# Voice models directory — will be auto-populated on first use,
# or mount a volume with pre-downloaded voices for faster startup.
RUN mkdir -p /app/voices
ENV PIPER_VOICES_DIR=/app/voices

RUN chown -R appuser /app/voices && chmod -R a+rX /app
USER appuser

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
