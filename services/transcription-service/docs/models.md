# Whisper Model Selection Guide

## Model Comparison

| Model | GPU VRAM (INT8) | CPU RAM (INT8) | Quality | Speed | Multilingual |
|-------|-----------------|----------------|---------|-------|--------------|
| **large-v3-turbo** | ~2.1 GB | ~6-8 GB | Excellent | Very Fast | Yes |
| **medium** | ~1-1.5 GB | ~2-4 GB | Very Good | Fast | Yes |
| **small** | ~0.5-1 GB | ~1-2 GB | Good | Very Fast | Yes |
| **base** | ~150 MB | ~300-600 MB | Good | Extremely Fast | Yes |
| **tiny** | ~75 MB | ~150-300 MB | Basic | Fastest | Yes |

All models are multilingual (99+ languages).

## Recommended: large-v3-turbo + INT8

- **GPU VRAM**: ~2.1 GB (validated)
- **Quality**: Excellent (95-98% accuracy)
- **Speed**: Very fast (>10x real-time)

## Model Selection by GPU VRAM

| Your GPU VRAM | Recommended Model | Compute Type | Expected VRAM |
|---------------|-------------------|--------------|---------------|
| 8+ GB | large-v3-turbo | INT8 | ~2.1 GB |
| 4-8 GB | large-v3-turbo | INT8 | ~2.1 GB |
| 2-4 GB | medium | INT8 | ~1-1.5 GB |
| 1-2 GB | small | INT8 | ~0.5-1 GB |
| CPU Only | medium | INT8 | ~2-4 GB RAM |

## Why INT8 Quantization?

**GPU benefits:**
- 50-60% VRAM reduction (6-8 GB -> 2-3 GB for large models)
- Still uses GPU acceleration (faster than CPU)
- Minimal accuracy loss (~1-2% WER increase)
- Enables larger models on smaller GPUs

**CPU benefits:**
- 2-4x speedup vs float32
- 50% memory reduction (6-8 GB -> 3-4 GB)
- Real-time capable (2-4x RT speed)
