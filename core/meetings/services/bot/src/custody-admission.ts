/**
 * Eval-side admission for one completed captured-signal custody root.
 *
 * The product bot deliberately preserves its normal exit semantics when an
 * optional telemetry sink fails. Evidence-producing harnesses have a stricter
 * postcondition: exactly one persisted receipt must survive the worker and a
 * fresh reader must validate the receipt, every captured-signal.v1 record, and
 * the content digest. hot-bot invokes this after the worker exits.
 */
import { readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  SignalCustodyError,
  directorySignalCustody,
  isSignalCustodyError,
  type CaptureSignalCustodyReceipt,
} from './telemetry-custody.js';

export async function admitCustodyEvidence(
  rootDir: string,
): Promise<CaptureSignalCustodyReceipt> {
  const root = resolve(rootDir);
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      throw new SignalCustodyError(
        'stored-object-missing',
        `stored-object-missing: custody root ${root}`,
        { cause: error },
      );
    }
    throw new SignalCustodyError(
      'io-fault',
      `could not inspect custody root ${root}: ${String(error)}`,
      { cause: error },
    );
  }

  const digests = entries
    .filter((entry) => entry.isDirectory() && /^[0-9a-f]{64}$/.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  if (digests.length === 0) {
    throw new SignalCustodyError(
      'stored-object-missing',
      `stored-object-missing: no custody receipt under ${root}`,
    );
  }
  if (digests.length !== 1) {
    throw new SignalCustodyError(
      'stored-object-incomplete',
      `stored-object-incomplete: expected one session under ${root}; found ${digests.length}`,
    );
  }

  // Construct after worker teardown: this reader owns no recorder state.
  const reader = directorySignalCustody(root);
  const receipt = await reader.load(digests[0]);
  const bytes = await reader.read(receipt);
  if (bytes.byteLength !== receipt.bytes) {
    throw new SignalCustodyError(
      'stored-object-incomplete',
      `stored-object-incomplete: independent readback length mismatch for ${receipt.key}`,
    );
  }
  return receipt;
}

async function main(): Promise<void> {
  const root = process.argv[2];
  if (!root) {
    console.error('usage: custody-admission <custody-root>');
    process.exitCode = 2;
    return;
  }
  try {
    const receipt = await admitCustodyEvidence(root);
    console.log(
      `CUSTODY_ADMITTED receipt=${JSON.stringify(receipt)} independent_readback=true`,
    );
  } catch (error) {
    if (isSignalCustodyError(error)) {
      console.error(
        `CUSTODY_ADMISSION_RED source=${error.source} kind=${error.kind}: ${error.message}`,
      );
      process.exitCode = 4;
      return;
    }
    console.error(`CUSTODY_ADMISSION_RED source=unexpected: ${String(error)}`);
    process.exitCode = 1;
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath === fileURLToPath(import.meta.url)) {
  void main();
}
