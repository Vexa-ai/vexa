# WhisperLive Performance Optimization PRD

## Executive Summary

This PRD outlines the optimization strategy for WhisperLive to maximize throughput and concurrent connections while maintaining fair distribution of transcription resources using a single medium model. All configuration parameters have been centralized in `settings.py` as the single source of truth, enabling systematic performance tuning.

## Current State

- **Configuration Centralized**: All transcription parameters moved from scattered locations to `settings.py`
- **Environment Variable Support**: 67 parameters configurable via `WL_*` environment variables
- **Single Source of Truth**: No more fallbacks or hardcoded values in `server.py` or `transcriber.py`
- **Ready for Optimization**: 30+ transcription parameters available for tuning

## Optimization Objectives

### Primary Goals
1. **Maximize Throughput**: Increase transcriptions per minute
2. **Scale Concurrent Connections**: Serve more clients simultaneously
3. **Fair Resource Distribution**: Ensure no single client monopolizes resources
4. **Maintain Quality**: Preserve acceptable transcription accuracy

### Success Metrics
- **Throughput**: Transcriptions per minute (target: 2x improvement)
- **Concurrency**: Maximum stable concurrent connections (target: 3x improvement)
- **Latency**: End-to-end transcription latency (maintain <2s)
- **Resource Utilization**: GPU/CPU efficiency (target: >80% utilization)

## Optimization Strategy

### Phase 1: Quick Wins (Immediate Impact)

#### 1. Decoding Simplification (Impact: 10/10)
- **BEAM_SIZE**: Reduce from 5 to 1 (greedy decoding)
  - **Rationale**: Eliminates beam search overhead, 3-5x decode speedup
  - **Trade-off**: Minor quality reduction, acceptable for real-time
  - **Implementation**: `BEAM_SIZE = 1`

- **BEST_OF**: Reduce from 5 to 1
  - **Rationale**: Removes parallel candidate exploration
  - **Trade-off**: Less robust against noise, faster processing
  - **Implementation**: `BEST_OF = 1`

- **TEMPERATURES**: Use single value `[0.0]`
  - **Rationale**: Disables multi-temperature sampling workflow
  - **Trade-off**: Less diversity in outputs, significant speedup
  - **Implementation**: `TEMPERATURES = [0.0]`

#### 2. Timestamp Elimination (Impact: 9/10)
- **WORD_TIMESTAMPS**: Keep False
  - **Rationale**: Eliminates word-level alignment computation
  - **Trade-off**: No word-level timing, major speedup
  - **Implementation**: `WORD_TIMESTAMPS = False`

- **WITHOUT_TIMESTAMPS**: Keep True
  - **Rationale**: Skips global timestamping pass
  - **Trade-off**: No segment timing, measurable performance gain
  - **Implementation**: `WITHOUT_TIMESTAMPS = True`

#### 3. Audio Batching Optimization (Impact: 7/10)
- **MIN_AUDIO_S**: Increase from 1.0 to 1.5-2.0 seconds
  - **Rationale**: Fewer, larger requests improve GPU amortization
  - **Trade-off**: Slightly higher latency, better throughput
  - **Implementation**: `MIN_AUDIO_S = 1.5`

- **BATCH_SIZE**: Increase from 8 to 16-32
  - **Rationale**: Higher decode parallelism across clients
  - **Trade-off**: Higher GPU memory usage, better utilization
  - **Implementation**: `BATCH_SIZE = 16` (monitor GPU memory)

#### 4. VAD Optimization (Impact: 7/10)
- **VAD_NO_SPEECH_THRESH**: Increase from 0.6 to 0.65
  - **Rationale**: Suppress borderline speech, reduce wasted decodes
  - **Trade-off**: May miss quiet speech, fewer false positives
  - **Implementation**: `VAD_NO_SPEECH_THRESH = 0.65`

- **VAD_ONSET**: Increase from 0.5 to 0.55
  - **Rationale**: Reduce false positives on background noise
  - **Trade-off**: May miss soft speech starts, cleaner segments
  - **Implementation**: `VAD_ONSET = 0.55`

### Phase 2: Secondary Optimizations (Moderate Impact)

#### 5. Context Processing (Impact: 6/10)
- **CONDITION_ON_PREVIOUS_TEXT**: Consider False
  - **Rationale**: Reduces context recomputation overhead
  - **Trade-off**: Less context awareness, faster processing
  - **Implementation**: `CONDITION_ON_PREVIOUS_TEXT = False`

#### 6. Output Loop Optimization (Impact: 6/10)
- **SAME_OUTPUT_THRESHOLD**: Reduce from 10 to 3-5
  - **Rationale**: Cuts CPU spin, advances windows sooner
  - **Trade-off**: May produce choppier output, less CPU waste
  - **Implementation**: `SAME_OUTPUT_THRESHOLD = 3`

#### 7. Sleep Optimization (Impact: 5/10)
- **REPEATED_OUTPUT_SLEEP_S**: Increase from 0.1 to 0.15-0.25s
  - **Rationale**: Reduces CPU churn on stabilized output
  - **Trade-off**: Slightly higher latency, less CPU usage
  - **Implementation**: `REPEATED_OUTPUT_SLEEP_S = 0.2`

### Phase 3: Fine-tuning (Small Impact)

#### 8. Language Detection (Impact: 4/10)
- **LANGUAGE_DETECTION_SEGMENTS**: Reduce from 10 to 3-5
  - **Rationale**: Detect language once, then lock
  - **Trade-off**: Less adaptive, faster processing
  - **Implementation**: `LANGUAGE_DETECTION_SEGMENTS = 3`

#### 9. Payload Optimization (Impact: 3/10)
- **SEND_LAST_N_SEGMENTS**: Reduce from 10 to 5
  - **Rationale**: Lower per-update JSON/network overhead
  - **Trade-off**: Less context in updates, smaller payloads
  - **Implementation**: `SEND_LAST_N_SEGMENTS = 5`

- **PICK_PREVIOUS_SEGMENTS**: Reduce from 2 to 1
  - **Rationale**: Reduce payload size under many clients
  - **Trade-off**: Less context, smaller updates
  - **Implementation**: `PICK_PREVIOUS_SEGMENTS = 1`

## Implementation Plan

### Step 1: Baseline Measurement
```bash
# Measure current performance
# - Transcriptions per minute
# - Maximum concurrent connections
# - GPU/CPU utilization
# - Memory usage
```

### Step 2: Apply Quick Wins
```python
# settings.py optimizations
BEAM_SIZE = 1
BEST_OF = 1
TEMPERATURES = [0.0]
WORD_TIMESTAMPS = False
WITHOUT_TIMESTAMPS = True
MIN_AUDIO_S = 1.5
BATCH_SIZE = 16
VAD_NO_SPEECH_THRESH = 0.65
VAD_ONSET = 0.55
```

### Step 3: Measure Impact
- Compare throughput before/after
- Monitor quality degradation
- Test concurrent connection limits

### Step 4: Iterative Refinement
- Apply secondary optimizations based on results
- Fine-tune parameters based on real-world usage
- Monitor for quality regressions

## Risk Mitigation

### Quality Risks
- **Mitigation**: A/B testing with quality metrics
- **Fallback**: Gradual parameter adjustment
- **Monitoring**: Real-time quality assessment

### Stability Risks
- **Mitigation**: Incremental deployment
- **Fallback**: Rollback to previous settings
- **Monitoring**: Error rate tracking

### Resource Risks
- **Mitigation**: GPU memory monitoring
- **Fallback**: Reduce BATCH_SIZE if OOM
- **Monitoring**: Resource utilization alerts

## Testing Strategy

### Load Testing
- **Concurrent Connections**: Test 5, 10, 15, 20+ clients
- **Sustained Load**: Run for extended periods
- **Resource Monitoring**: Track GPU/CPU/memory usage

### Quality Testing
- **Accuracy Metrics**: Compare transcription quality
- **Latency Testing**: Measure end-to-end delays
- **Edge Cases**: Test with various audio qualities

### Performance Testing
- **Throughput Measurement**: Transcriptions per minute
- **Scalability Testing**: Maximum stable connections
- **Resource Efficiency**: Utilization percentages

## Success Criteria

### Phase 1 Success
- **Throughput**: 2x improvement in transcriptions/minute
- **Concurrency**: 3x improvement in stable connections
- **Quality**: <5% accuracy degradation
- **Latency**: Maintain <2s end-to-end

### Phase 2 Success
- **Additional Throughput**: 1.5x improvement over Phase 1
- **Resource Efficiency**: >80% GPU utilization
- **Stability**: <1% error rate under load

### Phase 3 Success
- **Fine-tuning**: Optimized for specific use cases
- **Monitoring**: Comprehensive performance tracking
- **Documentation**: Complete optimization guide

## Monitoring and Alerting

### Key Metrics
- Transcriptions per minute
- Concurrent active connections
- GPU/CPU utilization
- Memory usage
- Error rates
- Average latency

### Alerting Thresholds
- GPU utilization >90%
- Memory usage >85%
- Error rate >2%
- Latency >3s average

## Rollback Plan

### Immediate Rollback
- Revert `settings.py` to previous values
- Restart services
- Monitor for stability

### Gradual Rollback
- Disable specific optimizations
- Monitor impact
- Adjust parameters incrementally

## Future Considerations

### Advanced Optimizations
- **Model Quantization**: INT8/FP16 precision
- **TensorRT Integration**: GPU-specific optimizations
- **Dynamic Batching**: Adaptive batch sizing
- **Load Balancing**: Multi-instance deployment

### Scaling Strategies
- **Horizontal Scaling**: Multiple WhisperLive instances
- **Model Sharding**: Different models per instance
- **Caching**: Transcription result caching
- **CDN Integration**: Audio preprocessing

## Conclusion

This optimization strategy provides a systematic approach to improving WhisperLive performance while maintaining quality and stability. The phased approach allows for controlled risk management and iterative improvement based on real-world performance data.

The centralized configuration in `settings.py` enables rapid experimentation and easy rollback, making this optimization process both effective and safe.
