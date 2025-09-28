# WhisperLive Optimization Harness

A comprehensive testing and optimization framework for WhisperLive server performance. This harness provides deterministic testing with static audio samples, real-time metrics collection, quality assessment, and parameter sweep capabilities.

## 🎯 Features

- **🧪 Deterministic Testing**: Static recorded audio fixtures + golden transcripts
- **🌐 WebSocket Load Testing**: N concurrent connections streaming audio in parallel
- **📈 Real-time Metrics**: 10-second sliding window metrics → pandas DataFrame → CSV
- **✅ Quality Assessment**: Transcript comparison vs goldens (string metrics + LLM judge)
- **🧰 One-command Execution**: End-to-end runner with live dashboard
- **🔄 Parameter Sweeps**: Automated testing across parameter ranges
- **📊 Live Dashboard**: Rich console display with sparklines and real-time updates

## 🚀 Quick Start

### 1. Installation

```bash
# Install dependencies
make install

# Or manually
pip install -r requirements.txt
```

### 2. Start WhisperLive Server

```bash
# In the parent directory
cd ..
docker-compose up -d whisperlive-server

# Or run directly
python run_server.py --port 9090 --backend faster_whisper
```

### 3. Setup Data

```bash
# Create data directories
make setup-data

# Download YouTube samples (requires WhisperLive server running)
python scripts/fetch_youtube_samples.py \
  --urls "https://www.youtube.com/watch?v=example1,https://www.youtube.com/watch?v=example2" \
  --output data/audio \
  --goldens data/goldens
```

### 4. Run Optimization Test

```bash
# Run baseline test
make run-baseline

# Run greedy decoding test
make run-greedy

# Run parameter sweep
make run-sweep
```

## 📁 Project Structure

```
whisperlive-opt/
├── data/                          # Test data
│   ├── audio/                     # Audio samples (16kHz mono WAV)
│   ├── goldens/                   # Golden transcripts (.txt)
│   └── manifest.csv               # Sample mapping
├── harness/                       # Core testing framework
│   ├── ws_client.py              # WebSocket client for load testing
│   ├── metrics.py                # Metrics collection and analysis
│   ├── collector.py              # Test orchestration
│   ├── compare_simple.py         # Simple text quality metrics
│   ├── compare_llm.py            # LLM judge for quality assessment
│   ├── logging_live.py           # Live dashboard with rich console
│   └── runner.py                 # Main runner and CLI
├── configs/                       # Test configurations
│   ├── baseline.yaml             # Default configuration
│   ├── greedy.yaml               # Greedy decoding optimization
│   ├── beam_search.yaml          # Beam search for quality
│   ├── sweep_num_workers.yaml    # Parameter sweep example
│   └── judge_prompt.md           # LLM judge prompt template
├── scripts/                       # Utility scripts
│   └── fetch_youtube_samples.py  # YouTube audio extraction
├── results/                       # Test results (timestamped)
├── requirements.txt               # Python dependencies
├── Makefile                      # Convenient targets
└── README.md                     # This file
```

## ⚙️ Configuration

### Basic Configuration (baseline.yaml)

```yaml
server:
  ws_url: "ws://localhost:9090/ws"
  language: "en"
  model: "small"                  # Recommended: "small" for consistent performance

run:
  concurrency: 2                  # Optimal: 2 connections for best throughput
  frame_ms: 20                    # Audio frame size (ms)
  warmup_s: 2                     # Warmup duration (s) - reduced for faster testing
  run_s: 10                       # Main test duration (s) - reduced for faster testing
  repeat_audio: true              # Repeat samples if needed

metrics:
  lambda: 0.5                     # Penalty weight for variance
  latency_slo: 2.0               # Latency SLO threshold (s)
  drop_slo: 0.02                 # Drop rate SLO (2%)

quality:
  enable_simple: true             # Enable string metrics
  enable_llm: false              # Enable LLM judge
```

**Performance Notes**:
- **Concurrency**: 2 connections optimal (303 events/10s total throughput)
- **Model**: "small" recommended over "medium" for better fairness
- **Duration**: Reduced times for faster iteration during development

### Server Parameters

Server-side parameters are set via environment variables or server startup flags:

```bash
# Environment variables
export WL_COMPUTE_TYPE="float16"
export WL_BEAM_SIZE="1"
export WL_NUM_WORKERS="4"
export WL_MIN_AUDIO_S="1.0"

# Or server flags
python run_server.py --port 9090 --backend faster_whisper \
  --beam_size 1 --num_workers 4 --min_audio_s 1.0
```

## 🎮 Usage

### Makefile Targets

```bash
# Quick tests
make run-baseline          # Baseline configuration
make run-greedy           # Greedy decoding
make run-beam             # Beam search
make run-sweep            # Parameter sweep

# Data preparation
make setup-data           # Create data directories
make fetch-samples        # Download YouTube samples
make prep-audio          # Prepare audio samples

# Development
make install-dev          # Install dev dependencies
make test                 # Run unit tests
make lint                 # Code linting
make format              # Code formatting

# Utilities
make status              # Check system status
make clean               # Clean temporary files
make help                # Show all targets
```

### Direct Python Usage

```bash
# Single test run
python -m harness.runner --config configs/baseline.yaml --out results/

# With LLM judge
python -m harness.runner --config configs/beam_search.yaml --out results/ --llm-judge

# Parameter sweep
python -m harness.runner --config configs/sweep_num_workers.yaml --out results/

# Dry run (validate config)
python -m harness.runner --config configs/baseline.yaml --dry-run
```

### YouTube Sample Extraction

```bash
# Extract audio and generate transcripts
python scripts/fetch_youtube_samples.py \
  --urls "https://www.youtube.com/watch?v=dQw4w9WgXcQ,https://www.youtube.com/watch?v=example2" \
  --output data/audio \
  --goldens data/goldens \
  --whisper-server ws://localhost:9090/ws \
  --max-duration 300
```

## 📊 Metrics and Scoring

### Primary Metrics

- **μ (Throughput)**: Mean transcript events per connection (10s window)
- **σ (Fairness)**: Standard deviation of transcript events
- **J (Composite Score)**: `μ - λ·σ - P` (higher is better)

### Transcription Server Performance Characteristics

Based on comprehensive testing with the WhisperLive optimization harness, we've identified key performance characteristics:

#### Throughput Ceiling
- **Hard Limit**: ~279-303 transcript events per 10 seconds total
- **Per-Connection**: ~150 events/conn/10s at optimal concurrency
- **Event Content**: Each event contains ~6.5 words (~39 characters) of transcribed text
- **Processing Rate**: ~30 events/second total system capacity

#### Concurrency Scaling
| Connections | Per-Conn Throughput | Total Throughput | Fairness (σ) | Composite Score |
|-------------|-------------------|------------------|--------------|----------------|
| 1           | 279.00            | 279.00           | 0.00         | 279.00         |
| 2           | 151.50            | 303.00           | 0.50         | 151.25         |
| 4           | 69.75             | 279.00           | 4.32         | 67.59          |
| 10          | 16.90             | 169.00           | 3.36         | 15.22          |

#### Key Findings
- **Optimal Concurrency**: 2 connections provide best total throughput (303 events/10s)
- **Scaling Behavior**: Beyond 2 connections, total throughput decreases
- **Fairness Degradation**: High concurrency causes significant variance between connections
- **Latency Stability**: p95 latency remains consistent (~0.02s) across all concurrency levels

#### Model Performance Comparison
| Model  | Per-Conn Throughput | Total Throughput | Fairness (σ) | Composite Score |
|--------|-------------------|------------------|--------------|----------------|
| Small  | 151.50            | 303.00           | 0.50         | 151.25         |
| Medium | 150.00            | 300.00           | 29.00        | 135.50         |

**Model Insights**:
- Small model provides more consistent performance (better fairness)
- Medium model shows higher variance between connections
- Throughput is nearly identical between models
- Small model recommended for production use

#### Real-Time Streaming Characteristics
- **Frame Rate**: 20ms audio frames
- **Event Generation**: ~67ms intervals (15 events/second per connection)
- **Content Pattern**: Progressive transcription as audio is processed
- **Audio Encoding**: float32 format expected by server
- **WebSocket Protocol**: JSON messages with `{"uid": "conn_id", "segments": [...]}` format

### Penalties (P)

- p95 latency > SLO: +0.5
- Drop rate > 2%: +0.5  
- GPU utilization >95%: +0.25
- Connection loss: +5.0

### Quality Metrics

#### Simple Text Metrics
- Character Error Rate (CER)
- Word Error Rate (WER)
- Token F1 Score
- Jaccard Similarity
- Longest Common Subsequence

#### LLM Judge Assessment
- **Fidelity** (0-5): Semantic agreement with reference
- **Omissions** (0-5): Missing content (lower better)
- **Insertions** (0-5): Hallucinated content (lower better)
- **Overall** (0-5): Overall utility

## 📈 Live Dashboard

The live dashboard provides real-time monitoring during test execution:

```
WhisperLive Isolated Test  |  cfg: compute=float16, beam=1, workers=4, min_audio_s=1.0
WS: ws://localhost:9090/ws  |  Concurrency: 32  |  Frame: 20ms  |  Warmup: 20s  Run: 120s
Audio: data/manifest.csv  |  Language: en

[ t=00:41 / 02:00 ]  active=32/32 ✓ STEADY

THROUGHPUT (10s):   μ=6.8   σ=1.1   J(λ=0.5)=6.2   penalties=0.0
LATENCY:                   p50=0.82s   p95=1.94s   drops=0.6%
GPU:                       util=77%   vram=16.3GB / 24GB

TOP-5 SLOW/QUIET:
  #07  C10s=3   rate=0.30/s   p95=2.10s   drops=1.8%   meeting=teams:abc...
  #21  C10s=4   rate=0.40/s   p95=1.97s   drops=0.0%   meeting=meet:def...

SPARKLINES:
  μ10s: ▂▃▄▅▆▆▇▇█▇▇▇
  p95 : ▅▅▄▃▃▃▃▃▃▂▂
```

## 📁 Output Files

Each test run creates a timestamped results directory:

```
results/2025-09-27T10-00-00Z/
├── per_second.csv              # Per-second aggregated metrics
├── per_conn.csv                # Per-connection detailed metrics
├── run.log.jsonl               # Real-time metrics log
├── transcripts/                # Final transcripts per connection
│   ├── conn_00.txt
│   ├── conn_01.txt
│   └── ...
├── quality_simple.json         # Simple text quality metrics
├── judge_llm.json              # LLM judge assessment
└── summary.md                  # Human-readable summary
```

### CSV Format

**per_second.csv**:
```csv
ts,t_rel,N,active,mu,sigma,J,lambda,p50_latency,p95_latency,drops_percent,gpu_util,vram_mb,penalties
1695816001.123,41,32,32,6.8,1.1,6.2,0.5,0.82,1.94,0.6,77,16300,0.0
```

**per_conn.csv**:
```csv
ts,conn_id,meeting_label,sample_id,C10s,rate_per_s,sends,recvs,drops,avg_latency,p95_latency,drops_percent
1695816001.123,conn_00,meeting_s01,s01,5,0.50,2400,120,2,0.85,1.88,0.8
```

## 🔧 Parameter Sweeps

### Example: num_workers Sweep

```yaml
# configs/sweep_num_workers.yaml
sweep:
  parameter: "num_workers"
  values: [1, 2, 4, 6, 8, 12]
  description: "Number of worker threads"

execution:
  restart_server_between_runs: true
  server_restart_delay: 5.0
  aggregate_results: true
  rank_by_metric: "composite_score"
```

Run with:
```bash
make run-sweep CONFIG=configs/sweep_num_workers.yaml
```

### Sweep Results

The sweep generates:
- `sweep_summary_num_workers.csv`: Tabular results
- `sweep_summary_num_workers.md`: Human-readable report
- Individual result directories for each parameter value

## 🐛 Troubleshooting

### Common Issues

**1. Connection Refused**
```bash
# Check if WhisperLive server is running
curl -I http://localhost:9090/health
# Or check WebSocket
websocat ws://localhost:9090/ws

# Check Docker container status
docker-compose ps whisperlive-server
docker-compose logs whisperlive-server
```

**2. Test Hanging/Timeout**
```bash
# Check for hanging processes
ps aux | grep harness.runner
# Kill hanging processes
kill <pid>

# The harness now includes timeout protection:
# - WebSocket disconnect: 5s timeout
# - Client cleanup: 10s timeout
```

**3. No Audio Samples**
```bash
# Check manifest file
head data/manifest.csv
# Verify audio files exist
ls -la data/audio/
```

**4. GPU Metrics Not Available**
```bash
# Install pynvml for GPU monitoring
pip install pynvml
# Or disable GPU monitoring
# Set gpu_sample_s: null in config
```

**5. LLM Judge Fails**
```bash
# Set API key
export OPENAI_API_KEY="your-key-here"
# Or use Anthropic
export ANTHROPIC_API_KEY="your-key-here"
```

### Debug Mode

```bash
# Enable debug logging
python -m harness.runner --config configs/baseline.yaml --out results/ --log-level DEBUG

# Validate configuration
python -m harness.runner --config configs/baseline.yaml --dry-run
```

## 🔬 Advanced Usage

### Custom Configurations

Create custom configs by copying and modifying existing ones:

```bash
cp configs/baseline.yaml configs/custom.yaml
# Edit configs/custom.yaml
make run-e2e CONFIG=configs/custom.yaml
```

### Integration with CI/CD

```yaml
# .github/workflows/whisperlive-optimization.yml
name: WhisperLive Optimization
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.9
      - name: Install dependencies
        run: make install
      - name: Run optimization test
        run: make quick-test
```

### Custom Quality Metrics

Extend quality assessment by implementing custom metrics in `harness/compare_custom.py`:

```python
from .compare_simple import QualityComparator

class CustomQualityComparator(QualityComparator):
    def calculate_custom_metric(self, predicted, golden):
        # Your custom metric implementation
        pass
```

## 📚 API Reference

### Core Classes

- **`WhisperLiveClient`**: WebSocket client for audio streaming
- **`MetricsCollector`**: Real-time metrics collection and aggregation
- **`TestCollector`**: Test orchestration and lifecycle management
- **`QualityComparator`**: Simple text quality metrics
- **`LLMJudge`**: LLM-based quality assessment
- **`LiveDashboard`**: Real-time console display

### Key Functions

- **`compare_quality_simple()`**: Run simple quality comparison
- **`compare_quality_llm()`**: Run LLM judge assessment
- **`create_client_pool()`**: Create WebSocket client pool
- **`WhisperLiveOptimizer.run()`**: Execute optimization test

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run linting: `make lint`
6. Submit a pull request

### Development Setup

```bash
# Clone and setup
git clone <repository>
cd whisperlive-opt
make install-dev

# Run tests
make test

# Format code
make format
```

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- Built for [WhisperLive](https://github.com/collabora/WhisperLive) optimization
- Uses [rich](https://github.com/Textualize/rich) for beautiful console output
- Audio processing with [librosa](https://librosa.org/)
- YouTube extraction with [yt-dlp](https://github.com/yt-dlp/yt-dlp)

## 📞 Support

For issues and questions:
- Create an issue in the repository
- Check the troubleshooting section
- Review the configuration examples

---

**Happy Optimizing! 🚀**
