/**
 * #956 C1/A1 — durable captured-signal custody, offline and deterministic.
 *
 * The first arm preserves the accepted RED: local close succeeds, then worker
 * deletion removes the only tape. The green arm uses the same real recorder,
 * deletes the worker directory, and independently reads identical bytes by a
 * content-addressed receipt. Missing and incomplete inputs fail with typed faults.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { createCaptureSignalRecorder } from './telemetry.js';
import type { Invocation } from './config.js';
import {
  SignalCustodyError,
  directorySignalCustody,
} from './telemetry-custody.js';

const invocation = {
  platform: 'jitsi',
  meetingUrl: 'https://meet.ffmuc.net/custody-red',
  botName: 'CustodyRed',
  nativeMeetingId: 'custody-red',
  connectionId: 'custody-red',
  redisUrl: 'redis://unused:6379',
  language: 'en',
} as Invocation;

const emitOneHint = (recorder: ReturnType<typeof createCaptureSignalRecorder>): void => {
  recorder.sink.captureHint?.({
    type: 'hint',
    name: 'Anna',
    t: 1718000000100,
    isEnd: false,
  });
};

async function typedKind(run: () => Promise<unknown>): Promise<string | undefined> {
  try {
    await run();
    return undefined;
  } catch (error) {
    assert(error instanceof SignalCustodyError, `expected SignalCustodyError, got ${String(error)}`);
    assert.equal(error.source, 'captured-signal-custody');
    return error.kind;
  }
}

const scratch = mkdtempSync(join(tmpdir(), 'vexa-custody-a1-'));
try {
  // RED control: a recorder can close successfully while its only bytes remain
  // inside the worker lifetime.
  const redWorker = join(scratch, 'red-worker');
  const redRecorder = createCaptureSignalRecorder(invocation, {
    dir: redWorker,
    flushMs: 5,
    now: () => 1718000000000,
    log: (message) => console.log(`[red-recorder] ${message}`),
  });
  emitOneHint(redRecorder);
  const noReceipt = await redRecorder.close();
  const redBytes = readFileSync(redRecorder.path);
  const redDigest = createHash('sha256').update(redBytes).digest('hex');
  assert.equal(redBytes.byteLength, 271, 'accepted exact RED oracle remains 271 bytes');
  assert.equal(noReceipt, null);
  rmSync(redWorker, { recursive: true, force: true });
  assert.equal(existsSync(redRecorder.path), false);
  console.log(`RED local_close=true sha256=${redDigest} bytes=${redBytes.byteLength} after_worker_delete.local_exists=false receipt=null`);

  // GREEN: the custody root is outside the worker directory. close() finalizes
  // by content, and independent readback happens only after worker deletion.
  const greenWorker = join(scratch, 'green-worker');
  const custodyRoot = join(scratch, 'off-worker-custody');
  const custody = directorySignalCustody(custodyRoot);
  const greenRecorder = createCaptureSignalRecorder(invocation, {
    dir: greenWorker,
    custody,
    flushMs: 5,
    now: () => 1718000000000,
    log: (message) => console.log(`[green-recorder] ${message}`),
  });
  emitOneHint(greenRecorder);
  const receipt = await greenRecorder.close();
  const repeated = await greenRecorder.close();
  assert(receipt);
  assert.deepEqual(repeated, receipt, 'close is idempotent and returns the same receipt');
  assert.equal(receipt.complete, true);
  assert.equal(receipt.records, 2);
  assert.match(receipt.digest, /^[0-9a-f]{64}$/);
  assert.equal(receipt.key, `${receipt.digest}/session.captured-signal.jsonl`);

  rmSync(greenWorker, { recursive: true, force: true });
  assert.equal(existsSync(greenRecorder.path), false, 'worker staging bytes are gone before readback');
  const freshReader = directorySignalCustody(custodyRoot);
  const durableReceipt = await freshReader.load(receipt.digest);
  assert.deepEqual(durableReceipt, receipt, 'receipt itself survives worker deletion');
  const independentlyRead = await freshReader.read(durableReceipt);
  assert.equal(createHash('sha256').update(independentlyRead).digest('hex'), receipt.digest);
  assert.equal(independentlyRead.byteLength, receipt.bytes);

  // A second recorder with byte-identical input admits to the same key and
  // returns a byte-identical receipt rather than creating a duplicate object.
  const retryWorker = join(scratch, 'retry-worker');
  const retryRecorder = createCaptureSignalRecorder(invocation, {
    dir: retryWorker,
    custody,
    flushMs: 5,
    now: () => 1718000000000,
    log: () => { /* quiet */ },
  });
  emitOneHint(retryRecorder);
  const retryReceipt = await retryRecorder.close();
  assert.deepEqual(retryReceipt, receipt, 'byte-identical admission is idempotent');

  // Admission refuses absence and truncation as distinct typed states.
  assert.equal(
    await typedKind(() => custody.admit({
      sourcePath: join(scratch, 'missing.captured-signal.jsonl'),
      expectedRecords: 1,
    })),
    'missing-source',
  );
  const incomplete = join(scratch, 'incomplete.captured-signal.jsonl');
  writeFileSync(incomplete, '{"type":"captured_signal_header","v":1}', 'utf8');
  assert.equal(
    await typedKind(() => custody.admit({ sourcePath: incomplete, expectedRecords: 1 })),
    'incomplete-source',
  );

  console.log(
    `GREEN receipt=${JSON.stringify(receipt)} after_worker_delete.local_exists=false independent_readback=true idempotent=true missing=missing-source incomplete=incomplete-source`,
  );
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
