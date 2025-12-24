FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

# Install Python and dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    curl \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install  
COPY requirements.txt .
# faster-whisper uses CTranslate2 which has built-in CUDA support (no PyTorch needed)
# CUDA 12.3.2 base image includes all necessary CUDA/cuDNN libraries
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application
COPY main.py .

# Create models directory
RUN mkdir -p /app/models

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["python3", "main.py"]

