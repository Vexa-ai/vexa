# Transcription Quality Testing (Multilingual + Noise + Silence)

This folder contains a **quality** (accuracy) test harness for the transcription service.

It is separate from the existing **load/perf** tools.

## What it tests

- **Multilingual accuracy**: `en, es, fr, de, it, pt, ru`
- **Noise robustness**: adds synthetic noise at multiple SNR levels
- **Silence behavior**: verifies the service does **not hallucinate** on silence
- **Regression tracking**: stores results in a small **SQLite** DB

## Quick start

### 1) Start the service

From `services/transcription-service/`:

```bash
docker-compose up -d --build
curl -s http://localhost:8083/health
```

### 2) Install test dependencies (host)

```bash
python3 -m venv .venv-quality
source .venv-quality/bin/activate
pip install -r tests/requirements-quality.txt
sudo apt-get update && sudo apt-get install -y ffmpeg
```

### 3) Generate a dataset (TTS + silence + noise)

```bash
python3 -m tests.quality.dataset_generate --languages en es fr de it pt ru
```

**Domain-labeled dataset (industry terms)**:
```bash
python3 -m tests.quality.dataset_generate \
  --languages en es fr de it pt ru \
  --domains healthcare finance legal software sales manufacturing \
  --phrases-per-domain 4
```

**With natural noise types and random silence** (up to 20 seconds):
```bash
python3 -m tests.quality.dataset_generate \
  --languages en es fr de it pt ru \
  --noise-types restaurant street office cafe \
  --max-silence 20.0
```

**With fixed silence lengths** (if you want deterministic):
```bash
python3 -m tests.quality.dataset_generate \
  --languages en es fr de it pt ru \
  --noise-types restaurant street office cafe \
  --lead-silence 1.0 \
  --trail-silence 1.0
```

This creates:
- `tests/quality_dataset/audio/*.wav` (all clips have silence+speech+silence structure)
- `tests/quality_dataset/manifest.jsonl`

**Features:**
- All speech clips include leading/trailing silence (tests VAD edge cases)
- Natural noise types: restaurant, street, office, cafe (pink/brown noise with frequency filtering)
- Multiple SNR levels: 20dB, 10dB, 5dB
- Industry domains are labeled in the manifest (`domain`) so you can report WER by domain

### 4) Run a quality run (stores results in SQLite)

```bash
python3 -m tests.quality.run_quality \
  --api-url http://localhost:8083/v1/audio/transcriptions \
  --dataset-dir tests/quality_dataset \
  --languages en es fr de it pt ru \
  --run-name local_run
```

**With Silero VAD validation** (recommended for silence detection):
```bash
python3 -m tests.quality.run_quality \
  --api-url http://localhost:8083/v1/audio/transcriptions \
  --dataset-dir tests/quality_dataset \
  --languages en es fr de it pt ru \
  --run-name local_run \
  --use-vad
```

**Filter by domains**:
```bash
python3 -m tests.quality.run_quality \
  --api-url http://localhost:8083/v1/audio/transcriptions \
  --dataset-dir tests/quality_dataset \
  --languages en es fr de it pt ru \
  --domains healthcare finance legal \
  --run-name local_run_domains \
  --use-vad
```

DB is stored at: `tests/quality_dataset/test_results.db`

### 5) (Optional) Run pytest gate

```bash
TRANSCRIPTION_API_URL=http://localhost:8083/v1/audio/transcriptions \
pytest -q
```

## Notes

- Dataset generation uses **gTTS** and therefore needs internet access.
- Audio conversion from MP3→WAV requires **ffmpeg**.
- **Silero VAD** is used for silence validation (optional `--use-vad` flag).
- Quality thresholds are per-language and per-noise-level (see `run_quality.py`).

## Quality Metrics

- **WER (Word Error Rate)**: Standard metric for transcription accuracy
- **CER (Character Error Rate)**: Useful for languages without clear word boundaries
- **Silence Detection**: Validates that silence clips don't produce hallucinations

## Thresholds

Default thresholds (can be adjusted in `run_quality.py`):
- Clean speech: WER ≤ 0.20 (20%)
- Noisy (SNR 20dB): WER ≤ 0.30 (30%)
- Noisy (SNR 10dB): WER ≤ 0.35 (35%)
- Noisy (SNR 5dB): WER ≤ 0.45 (45%)
- Silence: Must produce empty transcript

