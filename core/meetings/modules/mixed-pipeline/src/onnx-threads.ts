/**
 * How many threads onnxruntime may use for the pyannote segmenter.
 *
 * onnxruntime sizes its intra-op pool from the host's cores and ignores the container's CPU
 * quota. On a large Kubernetes node with a small Pod limit that means one busy thread per host
 * core competing for a couple of CPUs: the JS event loop starves, STT requests time out and
 * transcripts stall. So we cap the pool to what the container can actually run:
 *
 *   1. BOT_ONNX_THREADS (positive integer) — explicit override, used as given.
 *   2. cgroup v2 `cpu.max` ("<quota> <period>", "max" = unlimited), then cgroup v1
 *      `cpu.cfs_quota_us` / `cpu.cfs_period_us` (-1 = unlimited) → ceil(quota / period).
 *   3. The machine's CPU count (os.availableParallelism()).
 *
 * Cases 2 and 3 are clamped to [1, MAX_DEFAULT_THREADS]: the model is small, so more threads
 * buy little and cost the rest of the bot its CPU.
 */
import { readFileSync } from 'node:fs';
import { availableParallelism } from 'node:os';

export const MAX_DEFAULT_THREADS = 4;

const CGROUP_V2_CPU_MAX = '/sys/fs/cgroup/cpu.max';
const CGROUP_V1_QUOTA = '/sys/fs/cgroup/cpu/cpu.cfs_quota_us';
const CGROUP_V1_PERIOD = '/sys/fs/cgroup/cpu/cpu.cfs_period_us';

export interface OnnxThreadsInputs {
  env?: Record<string, string | undefined>;
  readFile?: (path: string) => string;
  cpuCount?: () => number;
}

export interface OnnxThreads {
  threads: number;
  source: 'env' | 'cgroup' | 'cpus';
  /** The container's CPU quota in CPUs, when one was found. */
  quotaCpus?: number;
}

function tryRead(readFile: (p: string) => string, path: string): string | undefined {
  try {
    return readFile(path).trim();
  } catch {
    return undefined;
  }
}

function positiveNumber(s: string | undefined): number | undefined {
  if (s === undefined || !/^\d+(\.\d+)?$/.test(s)) return undefined;
  const n = Number(s);
  return n > 0 ? n : undefined;
}

/** The container's CPU quota in CPUs, or undefined when unlimited or unknown. */
export function cgroupQuotaCpus(readFile: (p: string) => string): number | undefined {
  const v2 = tryRead(readFile, CGROUP_V2_CPU_MAX);
  if (v2 !== undefined) {
    const [quota, period] = v2.split(/\s+/);
    const q = positiveNumber(quota);
    const p = positiveNumber(period);
    return q !== undefined && p !== undefined ? q / p : undefined;
  }
  const q = positiveNumber(tryRead(readFile, CGROUP_V1_QUOTA));
  const p = positiveNumber(tryRead(readFile, CGROUP_V1_PERIOD));
  return q !== undefined && p !== undefined ? q / p : undefined;
}

export function resolveOnnxThreads(inputs: OnnxThreadsInputs = {}): OnnxThreads {
  const env = inputs.env ?? process.env;
  const readFile = inputs.readFile ?? ((p: string) => readFileSync(p, 'utf8'));
  const cpuCount = inputs.cpuCount ?? availableParallelism;

  const override = (env.BOT_ONNX_THREADS ?? '').trim();
  if (/^\d+$/.test(override) && Number(override) > 0) {
    return { threads: Number(override), source: 'env' };
  }

  const clamp = (n: number): number => Math.max(1, Math.min(MAX_DEFAULT_THREADS, Math.floor(n) || 1));

  const quotaCpus = cgroupQuotaCpus(readFile);
  if (quotaCpus !== undefined) {
    return { threads: clamp(Math.ceil(quotaCpus)), source: 'cgroup', quotaCpus };
  }
  return { threads: clamp(cpuCount()), source: 'cpus' };
}

/** ORT InferenceSession options for the chosen thread count. */
export function onnxSessionOptions(t: OnnxThreads): { intraOpNumThreads: number; interOpNumThreads: number } {
  return { intraOpNumThreads: t.threads, interOpNumThreads: 1 };
}

/** One log line in the bot's style, e.g. `[mixed] onnx threads=2 (cgroup quota 2.0 cpus)`. */
export function describeOnnxThreads(t: OnnxThreads): string {
  const why =
    t.source === 'env' ? 'BOT_ONNX_THREADS'
    : t.source === 'cgroup' ? `cgroup quota ${t.quotaCpus!.toFixed(1)} cpus`
    : 'cpu count';
  return `[mixed] onnx threads=${t.threads} (${why})`;
}
