# syntax=docker/dockerfile:1
# Transcription worker (GPU) — faster-whisper / CTranslate2 on CUDA. CTranslate2 ships its own
# CUDA support (no PyTorch); the cuda-cudnn runtime base provides the libs. Build context is the
# carve root (compose `context: ../..`) to match the other core services.
FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=3.12 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH \
    DEVICE=cuda

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip curl libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir uv==0.9.22
# The cuda base ships system python 3.10, but faster-whisper's onnxruntime needs cp311+.
# uv fetches a managed CPython 3.12 (UV_PYTHON) and builds /opt/venv on it.
RUN uv python install 3.12

WORKDIR /app

COPY core/meetings/services/transcription/pyproject.toml core/meetings/services/transcription/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY core/meetings/services/transcription/src ./src
RUN mkdir -p /app/models

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["python", "-m", "transcription"]
