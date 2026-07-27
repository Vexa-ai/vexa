/**
 * ALLOY: Focused pass-through contract for the capture/STT recording tap.
 *
 * Break caught: wrapping a transcribe closure drops its limiter execution
 * observer, so queue telemetry stops at the optional diagnostic adapter.
 */
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type {
  TranscriptionExecutionObserver,
  TranscriptionResult,
} from '@vexa/transcribe-whisper';
import { wrapTranscribeWithTap } from './telemetry.js';

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function run(): Promise<void> {
  const dir = mkdtempSync(join(tmpdir(), 'alloy-stt-tap-'));
  const sessionPath = join(dir, 'observer.captured-signal.jsonl');
  const observer: TranscriptionExecutionObserver = {
    waiting: () => {},
    started: () => {},
    finished: () => {},
  };
  let receivedObserver: TranscriptionExecutionObserver | undefined;
  const transcribe = async (
    _pcm: Float32Array,
    _prompt?: string,
    executionObserver?: TranscriptionExecutionObserver,
  ): Promise<TranscriptionResult> => {
    receivedObserver = executionObserver;
    return {
      text: 'observer pass-through',
      language: 'en',
      language_probability: 1,
      duration: 0.1,
      segments: [],
    };
  };

  try {
    const wrapped = wrapTranscribeWithTap(transcribe, sessionPath, () => {});
    const response = await wrapped(
      new Float32Array(1_600).fill(0.05),
      'previous words',
      observer,
    );

    assert.equal(receivedObserver, observer);
    assert.equal(response.text, 'observer pass-through');
    await sleep(25);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }

  console.log('PASS ALLOY STT tap forwards the exact limiter observer');
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
