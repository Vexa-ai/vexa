/**
 * onnx-threads — pins how many intra-op threads onnxruntime gets for the pyannote segmenter.
 *
 * Left alone, onnxruntime sizes its pool from the HOST's cores and ignores the container's CPU
 * quota: on a 128-core node with a 2-CPU Pod limit the bot ran 128 busy threads, starved its own
 * event loop and stopped producing transcripts. The helper must read the cgroup quota (or an
 * explicit override) and never hand onnxruntime more than the container can run.
 *
 *   tsx src/onnx-threads.test.ts
 */
import { resolveOnnxThreads, onnxSessionOptions } from './onnx-threads.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** A fake filesystem: path → contents; anything else throws like a missing file. */
const fs = (files: Record<string, string>) => (p: string): string => {
  if (p in files) return files[p];
  throw Object.assign(new Error(`ENOENT: ${p}`), { code: 'ENOENT' });
};
const V2 = '/sys/fs/cgroup/cpu.max';
const V1Q = '/sys/fs/cgroup/cpu/cpu.cfs_quota_us';
const V1P = '/sys/fs/cgroup/cpu/cpu.cfs_period_us';

const run = (env: Record<string, string | undefined>, files: Record<string, string>, cpus: number) =>
  resolveOnnxThreads({ env, readFile: fs(files), cpuCount: () => cpus });

// The measured case: 128-core host, Pod limit 2 CPUs.
{
  const r = run({}, { [V2]: '200000 100000\n' }, 128);
  check('cgroup v2 quota 2 CPUs on a 128-core host → 2 threads', r.threads === 2, JSON.stringify(r));
  check('source names the cgroup quota', r.source === 'cgroup', JSON.stringify(r));
  check('reports the quota in CPUs', r.quotaCpus === 2, JSON.stringify(r));
}
// Fractional quota rounds up, never to zero.
check('quota 1.5 CPUs → 2', run({}, { [V2]: '150000 100000' }, 128).threads === 2);
check('quota 0.5 CPUs → 1', run({}, { [V2]: '50000 100000' }, 128).threads === 1);
// Large quota is still clamped to the ceiling.
check('quota 16 CPUs → clamped to 4', run({}, { [V2]: '1600000 100000' }, 128).threads === 4);
// Unlimited quota falls back to the CPU count, clamped.
{
  const r = run({}, { [V2]: 'max 100000' }, 128);
  check('cpu.max "max" → cpu count clamped to 4', r.threads === 4 && r.source === 'cpus', JSON.stringify(r));
}
check('cpu.max "max" on a 2-core machine → 2', run({}, { [V2]: 'max 100000' }, 2).threads === 2);
// No cgroup files at all (macOS, bare metal).
check('no cgroup → cpu count', run({}, {}, 3).threads === 3);
check('no cgroup, 1 core → 1', run({}, {}, 1).threads === 1);
check('cpu count 0 → at least 1', run({}, {}, 0).threads === 1);
// cgroup v1 fallback.
check('cgroup v1 quota 2 CPUs → 2', run({}, { [V1Q]: '200000\n', [V1P]: '100000\n' }, 128).threads === 2);
check('cgroup v1 quota -1 (unlimited) → cpu count clamped', run({}, { [V1Q]: '-1', [V1P]: '100000' }, 128).threads === 4);
// Garbage in cpu.max is ignored, not trusted.
check('malformed cpu.max → cpu count', run({}, { [V2]: 'nonsense' }, 2).threads === 2);
// Explicit override wins over the cgroup and over the ceiling.
{
  const r = run({ BOT_ONNX_THREADS: '8' }, { [V2]: '200000 100000' }, 128);
  check('BOT_ONNX_THREADS=8 wins over cgroup and ceiling', r.threads === 8 && r.source === 'env', JSON.stringify(r));
}
check('BOT_ONNX_THREADS=1 → 1', run({ BOT_ONNX_THREADS: '1' }, {}, 128).threads === 1);
check('BOT_ONNX_THREADS=0 ignored', run({ BOT_ONNX_THREADS: '0' }, { [V2]: '200000 100000' }, 128).threads === 2);
check('BOT_ONNX_THREADS=abc ignored', run({ BOT_ONNX_THREADS: 'abc' }, { [V2]: '200000 100000' }, 128).threads === 2);
check('BOT_ONNX_THREADS=2.5 ignored', run({ BOT_ONNX_THREADS: '2.5' }, {}, 3).threads === 3);

// Session options handed to transformers.js / onnxruntime.
{
  const o = onnxSessionOptions({ threads: 2, source: 'cgroup', quotaCpus: 2 });
  check('session options carry intraOpNumThreads', o.intraOpNumThreads === 2, JSON.stringify(o));
  check('session options pin interOpNumThreads to 1', o.interOpNumThreads === 1, JSON.stringify(o));
}

if (failed > 0) {
  console.error(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log('\nonnx-threads: all checks passed');
