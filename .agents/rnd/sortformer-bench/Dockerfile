FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 sox git build-essential \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch (smaller, and guarantees no accidental GPU use)
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# NeMo ASR collection (brings Streaming Sortformer). Cython/numpy first to
# avoid build-time resolver thrash.
RUN pip install --no-cache-dir "numpy<2" Cython packaging soundfile
RUN pip install --no-cache-dir "nemo_toolkit[asr]"

WORKDIR /work
