/**
 * Durable custody for one completed captured-signal.v1 session.
 *
 * The recorder writes its hot-path bytes to a worker-local staging file. This
 * port moves the completed file across the worker-lifetime boundary and returns
 * a deterministic receipt. A production adapter may target a mounted volume or
 * object store; the directory adapter is the smallest real implementation and
 * the offline oracle for the contract.
 */
import { createHash, randomBytes } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { copyFile, link, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';

export type SignalCustodyErrorKind =
  | 'missing-source'
  | 'incomplete-source'
  | 'stored-object-missing'
  | 'stored-object-incomplete'
  | 'digest-mismatch'
  | 'io-fault';

/** P18 typed fault: callers can distinguish missing evidence from incomplete
 * evidence and from a broken custody dependency. */
export class SignalCustodyError extends Error {
  readonly source = 'captured-signal-custody';

  constructor(
    readonly kind: SignalCustodyErrorKind,
    message: string,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.name = 'SignalCustodyError';
  }
}

export function isSignalCustodyError(error: unknown): error is SignalCustodyError {
  return error instanceof SignalCustodyError;
}

export interface CaptureSignalAdmission {
  /** Completed worker-local staging file. */
  sourcePath: string;
  /** Header + frames + hints + boundaries the recorder says it wrote. */
  expectedRecords: number;
}

export interface CaptureSignalCustodyReceipt {
  type: 'captured-signal-custody-receipt';
  v: 1;
  complete: true;
  algorithm: 'sha256';
  /** Lowercase 64-hex content digest, without an algorithm prefix. */
  digest: string;
  bytes: number;
  records: number;
  /** Store-relative, deterministic object key. */
  key: string;
}

export interface SignalCustody {
  /** Admit one complete staging file. Repeating the same bytes returns the same receipt. */
  admit(input: CaptureSignalAdmission): Promise<CaptureSignalCustodyReceipt>;
  /** Load a durable receipt in a fresh process by content digest. */
  load(digest: string): Promise<CaptureSignalCustodyReceipt>;
  /** Independent readback with receipt, completeness, and digest verification. */
  read(receipt: CaptureSignalCustodyReceipt): Promise<Uint8Array>;
}

interface InspectedSignal {
  digest: string;
  bytes: number;
  records: number;
}

async function inspectSignal(
  path: string,
  missingKind: Extract<SignalCustodyErrorKind, 'missing-source' | 'stored-object-missing'>,
  incompleteKind: Extract<SignalCustodyErrorKind, 'incomplete-source' | 'stored-object-incomplete'>,
  expectedRecords?: number,
): Promise<InspectedSignal> {
  const hash = createHash('sha256');
  let bytes = 0;
  let records = 0;
  let lastByte: number | undefined;
  let line = '';
  let header: unknown;

  try {
    for await (const chunkValue of createReadStream(path)) {
      const chunk = chunkValue as Buffer;
      hash.update(chunk);
      bytes += chunk.byteLength;
      if (chunk.byteLength > 0) lastByte = chunk[chunk.byteLength - 1];

      for (const byte of chunk) {
        if (byte === 0x0a) {
          let record: unknown;
          try {
            record = JSON.parse(line);
          } catch (error) {
            throw new SignalCustodyError(incompleteKind, `${incompleteKind}: ${path} has invalid JSONL record ${records + 1}`, { cause: error });
          }
          if (records === 0) header = record;
          records++;
          line = '';
        } else {
          line += String.fromCharCode(byte);
        }
      }
    }
  } catch (error) {
    if (isSignalCustodyError(error)) throw error;
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') {
      throw new SignalCustodyError(missingKind, `${missingKind}: ${path}`, { cause: error });
    }
    throw new SignalCustodyError('io-fault', `could not inspect captured signal ${path}: ${String(error)}`, { cause: error });
  }

  if (bytes === 0 || lastByte !== 0x0a || records === 0) {
    throw new SignalCustodyError(incompleteKind, `${incompleteKind}: ${path} is empty or not newline-complete`);
  }
  if (expectedRecords !== undefined && records !== expectedRecords) {
    throw new SignalCustodyError(
      incompleteKind,
      `${incompleteKind}: ${path} has ${records} records; recorder expected ${expectedRecords}`,
    );
  }

  if (
    typeof header !== 'object'
    || header === null
    || (header as Record<string, unknown>).type !== 'captured_signal_header'
    || (header as Record<string, unknown>).v !== 1
  ) {
    throw new SignalCustodyError(incompleteKind, `${incompleteKind}: ${path} does not open with captured-signal.v1`);
  }

  return { digest: hash.digest('hex'), bytes, records };
}

function assertReceipt(receipt: CaptureSignalCustodyReceipt): void {
  const expectedKey = `${receipt.digest}/session.captured-signal.jsonl`;
  if (
    receipt.type !== 'captured-signal-custody-receipt'
    || receipt.v !== 1
    || receipt.complete !== true
    || receipt.algorithm !== 'sha256'
    || !/^[0-9a-f]{64}$/.test(receipt.digest)
    || receipt.key !== expectedKey
  ) {
    throw new SignalCustodyError('stored-object-incomplete', 'stored-object-incomplete: invalid custody receipt');
  }
}

/** Content-addressed filesystem adapter. `rootDir` must live outside the
 * ephemeral worker directory (for k8s, mount durable storage there). */
export function directorySignalCustody(rootDir: string): SignalCustody {
  const root = resolve(rootDir);
  const pathFor = (receipt: CaptureSignalCustodyReceipt): string => join(root, receipt.key);

  return {
    async admit(input: CaptureSignalAdmission): Promise<CaptureSignalCustodyReceipt> {
      const source = await inspectSignal(
        input.sourcePath,
        'missing-source',
        'incomplete-source',
        input.expectedRecords,
      );
      const receipt: CaptureSignalCustodyReceipt = {
        type: 'captured-signal-custody-receipt',
        v: 1,
        complete: true,
        algorithm: 'sha256',
        digest: source.digest,
        bytes: source.bytes,
        records: source.records,
        key: `${source.digest}/session.captured-signal.jsonl`,
      };
      const target = pathFor(receipt);
      await mkdir(dirname(target), { recursive: true });

      // Copy to a sibling temporary file, then link it into the deterministic
      // key atomically. A concurrent/idempotent admission wins with EEXIST; the
      // existing object is verified below before its receipt is returned.
      const temporary = join(dirname(target), `.incoming-${process.pid}-${randomBytes(8).toString('hex')}`);
      try {
        await copyFile(input.sourcePath, temporary);
        const copied = await inspectSignal(
          temporary,
          'stored-object-missing',
          'stored-object-incomplete',
          source.records,
        );
        if (copied.digest !== source.digest || copied.bytes !== source.bytes) {
          throw new SignalCustodyError('digest-mismatch', `digest-mismatch: staging copy for ${receipt.key}`);
        }
        try {
          await link(temporary, target);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        }
      } catch (error) {
        if (isSignalCustodyError(error)) throw error;
        throw new SignalCustodyError('io-fault', `could not admit ${receipt.key}: ${String(error)}`, { cause: error });
      } finally {
        await rm(temporary, { force: true }).catch(() => { /* best-effort temporary cleanup */ });
      }

      const stored = await inspectSignal(
        target,
        'stored-object-missing',
        'stored-object-incomplete',
        source.records,
      );
      if (stored.digest !== receipt.digest || stored.bytes !== receipt.bytes) {
        throw new SignalCustodyError('digest-mismatch', `digest-mismatch: stored object ${receipt.key}`);
      }
      const receiptPath = join(dirname(target), 'receipt.json');
      const receiptBody = JSON.stringify(receipt) + '\n';
      const receiptTemporary = join(dirname(target), `.receipt-${process.pid}-${randomBytes(8).toString('hex')}`);
      try {
        await writeFile(receiptTemporary, receiptBody, { encoding: 'utf8', flag: 'wx' });
        try {
          await link(receiptTemporary, receiptPath);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        }
      } catch (error) {
        throw new SignalCustodyError('io-fault', `could not persist receipt for ${receipt.key}: ${String(error)}`, { cause: error });
      } finally {
        await rm(receiptTemporary, { force: true }).catch(() => { /* best-effort temporary cleanup */ });
      }
      const persisted = await this.load(receipt.digest);
      if (JSON.stringify(persisted) !== JSON.stringify(receipt)) {
        throw new SignalCustodyError('stored-object-incomplete', `stored-object-incomplete: receipt mismatch for ${receipt.key}`);
      }
      return receipt;
    },

    async load(digest: string): Promise<CaptureSignalCustodyReceipt> {
      if (!/^[0-9a-f]{64}$/.test(digest)) {
        throw new SignalCustodyError('stored-object-incomplete', 'stored-object-incomplete: invalid receipt digest');
      }
      const receiptPath = join(root, digest, 'receipt.json');
      let parsed: unknown;
      try {
        parsed = JSON.parse(await readFile(receiptPath, 'utf8'));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
          throw new SignalCustodyError('stored-object-missing', `stored-object-missing: ${digest}/receipt.json`, { cause: error });
        }
        throw new SignalCustodyError('stored-object-incomplete', `stored-object-incomplete: ${digest}/receipt.json`, { cause: error });
      }
      assertReceipt(parsed as CaptureSignalCustodyReceipt);
      if ((parsed as CaptureSignalCustodyReceipt).digest !== digest) {
        throw new SignalCustodyError('stored-object-incomplete', `stored-object-incomplete: receipt digest mismatch for ${digest}`);
      }
      return parsed as CaptureSignalCustodyReceipt;
    },

    async read(receipt: CaptureSignalCustodyReceipt): Promise<Uint8Array> {
      assertReceipt(receipt);
      const target = pathFor(receipt);
      const stored = await inspectSignal(
        target,
        'stored-object-missing',
        'stored-object-incomplete',
        receipt.records,
      );
      if (stored.digest !== receipt.digest || stored.bytes !== receipt.bytes) {
        throw new SignalCustodyError('digest-mismatch', `digest-mismatch: stored object ${receipt.key}`);
      }
      try {
        return await readFile(target);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
          throw new SignalCustodyError('stored-object-missing', `stored-object-missing: ${receipt.key}`, { cause: error });
        }
        throw new SignalCustodyError('io-fault', `could not read ${receipt.key}: ${String(error)}`, { cause: error });
      }
    },
  };
}
