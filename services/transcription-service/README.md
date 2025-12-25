# Vexa-Compatible Transcription Service (PoC)

A production-ready, scalable transcription service compatible with Vexa's remote transcription mode. Features load balancing, auto-scaling with replicas, self-healing, and a single API entry point.

## Features

✅ **OpenAI Whisper API Compatible** - Works seamlessly with Vexa's RemoteTranscriber  
✅ **Load Balanced** - Nginx distributes requests across multiple workers  
✅ **Self-Healing** - Automatic health checks and failover  
✅ **Scalable** - Easy replica management (3 workers by default)  
✅ **Single API Entry** - One endpoint for all transcription requests  
✅ **GPU & CPU Support** - Optimized for both environments  
✅ **Docker-Native** - Easy deployment with docker-compose  

## Architecture

```
Client Request → Nginx Load Balancer (Port 8083)
                      ↓
         ┌────────────┼────────────┐
         ↓            ↓            ↓
    Worker 1      Worker 2      Worker 3
    (GPU/CPU)     (GPU/CPU)     (GPU/CPU)
```

## Quick Start

### GPU Deployment (Recommended)

```bash
# 1. Set optimal configuration (large-v3-turbo + INT8)
echo "MODEL_SIZE=large-v3-turbo" > .env
echo "DEVICE=cuda" >> .env
echo "COMPUTE_TYPE=int8" >> .env

# 2. Start all services
docker-compose up -d

# 3. Wait for services to start (1-2 minutes on first run)
docker-compose logs -f
# Wait for "Worker X ready - Model loaded successfully"

# 4. Check status
curl http://localhost:8083/health
```

### CPU Deployment (Testing/Development)

```bash
# Use CPU-optimized configuration
echo "MODEL_SIZE=medium" > .env
echo "DEVICE=cpu" >> .env
echo "COMPUTE_TYPE=int8" >> .env
echo "CPU_THREADS=4" >> .env

# Start services
docker-compose -f docker-compose.cpu.yml up -d
```

## API Usage

### Health Check

```bash
curl http://localhost:8083/health
```

### Transcribe Audio

```bash
curl -X POST http://localhost:8083/v1/audio/transcriptions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "file=@audio.wav" \
  -F "model=whisper-1" \
  -F "response_format=verbose_json" \
  -F "timestamp_granularities=segment"
```

### Response Format

```json
{
  "text": "Full transcript text here",
  "language": "en",
  "duration": 5.2,
  "segments": [
    {
      "start": 0.0,
      "end": 2.5,
      "text": "Hello everyone",
      "audio_start": 0.0,
      "audio_end": 2.5,
      "avg_logprob": -0.3,
      "compression_ratio": 1.2,
      "no_speech_prob": 0.1
    }
  ]
}
```

## Integration with Vexa

### 1. Start Transcription Service

```bash
cd vexa-transcription-service
docker-compose up -d
```

### 2. Configure Vexa

```bash
cd ../vexa

# Add to your .env file or export:
export REMOTE_TRANSCRIBER_URL=http://localhost:8083/v1/audio/transcriptions
export REMOTE_TRANSCRIBER_API_KEY=your_api_key_here
export REMOTE_TRANSCRIBER_MODEL=whisper-1
export REMOTE_TRANSCRIBER_TEMPERATURE=0
```

### 3. Start Vexa with Remote Backend

The WhisperLive service will automatically detect the remote backend configuration and use your transcription service instead of running local Whisper models.

## Configuration

### Model Selection by Resources

| Your GPU VRAM | Recommended Model | Compute Type | Expected VRAM | Quality |
|---------------|-------------------|--------------|---------------|---------|
| **8+ GB** | `large-v3-turbo` | INT8 | **~2.1 GB** ✅ | Excellent |
| **4-8 GB** | `large-v3-turbo` | INT8 | **~2.1 GB** ✅ | Excellent |
| **4 GB** (tight) | `medium` | INT8 | **~1-1.5 GB** | Very Good |
| **2-4 GB** | `medium` | INT8 | **~1-1.5 GB** | Very Good |
| **1-2 GB** | `small` | INT8 | **~0.5-1 GB** | Good |
| **CPU Only** | `medium` | INT8 | **~2-4 GB RAM** | Very Good |

**All models above are multilingual (99+ languages)** ✅

### Model Comparison

| Model | GPU VRAM (INT8) | CPU RAM (INT8) | Quality | Speed | Multilingual |
|-------|-----------------|----------------|---------|-------|--------------|
| **large-v3-turbo** | ~2.1 GB ✅ | ~6-8 GB | Excellent | Very Fast | ✅ Yes |
| **medium** | ~1-1.5 GB | ~2-4 GB | Very Good | Fast | ✅ Yes |
| **small** | ~0.5-1 GB | ~1-2 GB | Good | Very Fast | ✅ Yes |
| **base** | ~150 MB | ~300-600 MB | Good | Extremely Fast | ✅ Yes |
| **tiny** | ~75 MB | ~150-300 MB | Basic | Fastest | ✅ Yes |

**Recommended:** `large-v3-turbo` + INT8
- **GPU VRAM**: ~2.1 GB (validated)
- **Quality**: Excellent (95-98% accuracy)
- **Speed**: Very fast (>10x real-time)
- **Multilingual**: 99+ languages

### Why INT8 Quantization?

**GPU Benefits:**
- ✅ **50-60% VRAM reduction** (6-8GB → 2-3GB for large models)
- ✅ **Still uses GPU acceleration** (faster than CPU)
- ✅ **Minimal accuracy loss** (~1-2% WER increase)
- ✅ **Enables larger models** on smaller GPUs

**CPU Benefits:**
- ✅ **2-4x speedup** vs float32
- ✅ **50% memory reduction** (6-8GB → 3-4GB)
- ✅ **Minimal accuracy loss** (~1-2% WER increase)
- ✅ **Real-time capable** (2-4x RT speed)

### Environment Variables

```bash
# Worker Configuration
WORKER_ID=1                    # Unique worker identifier
MODEL_SIZE=large-v3-turbo     # Whisper model size (default: large-v3-turbo)
DEVICE=cuda                    # Device: cuda or cpu (default: cuda)
COMPUTE_TYPE=int8              # Compute type: int8, float16, float32 (default: int8)
CPU_THREADS=4                  # CPU threads (0 = auto-detect, default: 0)

# Load management / backpressure
# Recommended for WhisperLive streaming: FAIL_FAST_WHEN_BUSY=true (prefer latest buffered audio)
MAX_CONCURRENT_TRANSCRIPTIONS=2 # Max concurrent model calls per worker
MAX_QUEUE_SIZE=10               # Max waiting requests (ignored when FAIL_FAST_WHEN_BUSY=true)
FAIL_FAST_WHEN_BUSY=true        # Return 503 immediately if busy (lets upstream keep buffering/coalescing)
BUSY_RETRY_AFTER_S=1            # Retry-After header value (seconds) on 503
```

### Recommended Configurations

**Production GPU (High Quality):**
```env
MODEL_SIZE=large-v3-turbo
DEVICE=cuda
COMPUTE_TYPE=int8
```

**Production GPU (Efficient):**
```env
MODEL_SIZE=medium
DEVICE=cuda
COMPUTE_TYPE=int8
```

**CPU Deployment:**
```env
MODEL_SIZE=medium
DEVICE=cpu
COMPUTE_TYPE=int8
CPU_THREADS=4  # Set to number of physical CPU cores
```

## Monitoring

### Check Load Balancer Status

```bash
curl http://localhost:8083/lb-status
```

### Check Individual Workers

```bash
# Worker 1
docker exec transcription-worker-1 curl localhost:8000/health

# Worker 2
docker exec transcription-worker-2 curl localhost:8000/health

# Worker 3
docker exec transcription-worker-3 curl localhost:8000/health
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific worker
docker-compose logs -f transcription-worker-1

# Load balancer
docker-compose logs -f transcription-api
```

## Scaling

### Add More Workers

Edit `docker-compose.yml`:

```yaml
  transcription-worker-4:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      - WORKER_ID=4
      - MODEL_SIZE=${MODEL_SIZE:-base}
    # ... rest of config same as other workers
```

Update `nginx.conf`:

```nginx
upstream transcription_workers {
    least_conn;
    server transcription-worker-1:8000;
    server transcription-worker-2:8000;
    server transcription-worker-3:8000;
    server transcription-worker-4:8000;  # Add new worker
}
```

Restart:

```bash
docker-compose up -d
```

## Self-Healing Features

1. **Health Checks** - Each worker reports health status every 30s
2. **Automatic Failover** - Nginx routes around unhealthy workers
3. **Auto-Restart** - Docker restarts failed containers
4. **GPU Memory Management** - Automatic CUDA cache cleanup
5. **Retry Logic** - 3 automatic retries on worker failures

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker-compose logs

# Common fixes:
# 1. GPU not available? Use CPU mode:
docker-compose -f docker-compose.cpu.yml up -d

# 2. Port 8083 in use? Change it in docker-compose.yml

# 3. Out of memory? Use smaller model:
echo "MODEL_SIZE=medium" > .env
docker-compose up -d
```

### Workers Not Starting

```bash
# Check logs
docker-compose logs transcription-worker-1

# Common issues:
# - GPU not available: Use docker-compose.cpu.yml
# - Out of memory: Use smaller model (medium/small)
# - Port conflict: Change port in docker-compose.yml
# - CUDA/cuDNN errors: Ensure CUDA 12.3.2+ base image
```

### Load Balancer Errors

```bash
# Check nginx config
docker exec transcription-lb nginx -t

# Reload config
docker exec transcription-lb nginx -s reload

# Check worker connectivity
docker exec transcription-lb wget -O- http://transcription-worker-1:8000/health
```

### GPU Issues

```bash
# Verify GPU availability
docker run --rm --gpus all nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04 nvidia-smi

# Check NVIDIA runtime
docker info | grep -i nvidia

# Check GPU memory usage
docker exec transcription-worker-1 nvidia-smi
```

### Transcription Fails

```bash
# Test audio format (must be WAV, 16kHz, mono recommended)
ffmpeg -i your_audio.mp3 -ar 16000 -ac 1 audio_16k.wav

# Test with converted file
curl -X POST http://localhost:8083/v1/audio/transcriptions \
  -F "file=@audio_16k.wav" \
  -F "model=whisper-1" \
  -F "response_format=verbose_json"
```

## Testing

### Basic API Test

Run the comprehensive test suite:

```bash
./tests/test_transcription.sh
```

This validates:
- Health check endpoint
- Load balancer status
- Transcription API
- Response format (OpenAI Whisper API compatible)
- Vexa-specific fields (`audio_start`, `audio_end`)

### Load Testing

**Quick Start:**
```bash
# Run all load tests (single worker max + scaling)
./tests/run_load_test.sh all

# Test single worker max performance
./tests/run_load_test.sh single

# Test multi-worker scaling
./tests/run_load_test.sh scaling
```

**Load Test Metrics:**
- **Requests per second (RPS)** - Throughput
- **Response time** - Min, max, avg, P50, P95, P99 latencies
- **Real-time factor** - How fast audio is processed vs. its duration
- **Throughput** - Audio duration processed per second
- **Success/Error rates** - Reliability metrics
- **Scaling efficiency** - How well performance scales with workers

**Requirements:**
- `aiohttp`: `pip install aiohttp`
- Test audio file: `tests/test_audio.wav` (auto-generated)

For detailed load testing documentation, see `tests/LOAD_TESTING.md`.

## Performance Tips

1. **GPU Deployment**: Use `large-v3-turbo` + INT8 for best quality/speed balance
2. **CPU Deployment**: Use `medium` + INT8, set `CPU_THREADS` to number of cores
3. **Memory**: Monitor GPU memory with `nvidia-smi` or worker health endpoints
4. **Workers**: Start with 3 workers, scale based on load testing results
5. **Model Caching**: First request downloads model, subsequent requests are fast
6. **Real-time Factor**: Expect 10-20x RT factor on GPU, 2-4x on CPU

## API Compatibility

This service implements the OpenAI Whisper API specification and is compatible with:

- ✅ Vexa's RemoteTranscriber
- ✅ OpenAI Whisper API clients
- ✅ Standard HTTP transcription workflows

## License

MIT License - Use freely for commercial and non-commercial projects

## Files

### Core Files
- `main.py` - FastAPI application with transcription endpoints
- `docker-compose.yml` - GPU deployment configuration
- `docker-compose.cpu.yml` - CPU deployment configuration
- `Dockerfile` - GPU worker container
- `Dockerfile.cpu` - CPU worker container
- `nginx.conf` - Load balancer configuration
- `requirements.txt` - Python dependencies

### Test Files
- `tests/test_transcription.sh` - Comprehensive API test suite
- `tests/load_test.py` - Load testing script
- `tests/run_load_test.sh` - Load test runner helper
- `tests/scale_workers.sh` - Worker scaling helper
- `tests/test_audio.wav` - Pre-generated test audio
- `tests/generate_test_audio.py` - Test audio generator
- `tests/LOAD_TESTING.md` - Detailed load testing guide

## Support

For issues or questions about integration with Vexa, refer to:
- Vexa documentation: `/home/dima/dev/vexa/README.md`
- Vexa remote mode: Check WhisperLive remote_transcriber implementation



