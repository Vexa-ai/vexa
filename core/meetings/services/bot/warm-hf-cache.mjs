#!/usr/bin/env node
/**
 * warm-hf-cache.mjs — bake / verify the mixed-lane diarization model.
 *
 * The Zoom/Teams (mixed) lane segments speakers with
 * `onnx-community/pyannote-segmentation-3.0` (see
 * meetings/modules/mixed-pipeline/src/pyannote-segmenter.ts). The first
 * `from_pretrained` downloads it from HuggingFace — a multi-hundred-ms→seconds
 * stall we must NOT pay during a live meeting. So we warm the model into an
 * image-baked cache dir at BUILD time, then load it OFFLINE at runtime.
 *
 * Two modes, both driven by env:
 *   - WARM  (default, builder stage, network available):
 *       env.allowRemoteModels = true → downloads + caches into $VEXA_HF_CACHE.
 *       Retries with backoff, then DEGRADES GRACEFULLY (loud warn + exit 0) if
 *       HuggingFace is unreachable/rate-limiting — pre-baking is a first-meeting
 *       LATENCY optimization for a PUBLIC model the bot can fetch at runtime, so
 *       an external CDN blip must NOT hard-fail the release build.
 *   - VERIFY (VEXA_HF_OFFLINE=1, runtime, `docker run --network none`):
 *       env.allowRemoteModels = false → MUST load from $VEXA_HF_CACHE only.
 *       Any failure → non-zero exit → the bake we claimed is broken (STAYS FATAL).
 *
 * Exit 0 = model loaded OR (WARM only) warm-skip after retries; non-zero = a
 * VERIFY failure or a config error. VERIFY doubles as the offline proof.
 *
 * NB on module resolution: `@huggingface/transformers` is NOT a direct dep of
 * @vexa/bot — it's transitive through @vexa/mixed-pipeline, and only that
 * package's node_modules has it in the pnpm symlink farm. ESM resolves bare
 * imports relative to THIS file's location (the bot dir), which lacks it, so a
 * plain `import ... from '@huggingface/transformers'` fails. We anchor a
 * createRequire at the mixed-pipeline package (a fixed relative hop) and import
 * the resolved absolute path instead.
 */
import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';

const MODEL_ID = 'onnx-community/pyannote-segmentation-3.0';
const CACHE_DIR = process.env.VEXA_HF_CACHE;
const OFFLINE = process.env.VEXA_HF_OFFLINE === '1';

if (!CACHE_DIR) {
  console.error('[warm-hf-cache] FATAL: VEXA_HF_CACHE is not set');
  process.exit(2);
}

// Resolve @huggingface/transformers via the mixed-pipeline package (the only
// one that depends on it). bot dir → ../../modules/mixed-pipeline.
const here = dirname(fileURLToPath(import.meta.url));
const anchor = resolve(here, '../../modules/mixed-pipeline/package.json');
const req = createRequire(anchor);
const { AutoModel, AutoProcessor, env } = await import(
  pathToFileURL(req.resolve('@huggingface/transformers')).href
);

// Pin the cache to the baked dir and pick the mode.
env.cacheDir = CACHE_DIR;
env.allowLocalModels = true;
env.allowRemoteModels = !OFFLINE; // VERIFY mode forbids any remote fetch

console.log(`[warm-hf-cache] mode=${OFFLINE ? 'VERIFY(offline)' : 'WARM(download)'} cacheDir=${CACHE_DIR} model=${MODEL_ID}`);

const t0 = Date.now();
// WARM (build-time download) RETRIES then DEGRADES GRACEFULLY: HuggingFace rate-limits shared CI
// egress IPs with a 403, which would hard-fail the release for a PUBLIC model the bot can fetch at
// runtime anyway. Pre-baking is a first-meeting LATENCY optimization, not a correctness gate — so an
// external CDN blip must not block a build. We retry with exponential backoff, and only if every
// attempt fails do we WARN LOUDLY and continue (exit 0): the image ships without the warm cache and
// the bot cold-fetches the model on its first Zoom/Teams meeting (Google Meet is unaffected).
//
// VERIFY (OFFLINE, `--network none`) STAYS FATAL: no network, no retry — a failure there means a
// bake we CLAIMED to have made can't load, i.e. a genuinely broken image. That must fail loudly.
const WARM_ATTEMPTS = OFFLINE ? 1 : 4;
let lastErr;
for (let attempt = 1; attempt <= WARM_ATTEMPTS; attempt++) {
  try {
    const model = await AutoModel.from_pretrained(MODEL_ID, { device: 'cpu' });
    const processor = await AutoProcessor.from_pretrained(MODEL_ID);
    if (!model || !processor) throw new Error('from_pretrained returned empty');
    console.log(`[warm-hf-cache] OK: model + processor loaded in ${Date.now() - t0}ms (attempt ${attempt})`);
    lastErr = undefined;
    break;
  } catch (err) {
    lastErr = err;
    console.error(`[warm-hf-cache] load attempt ${attempt}/${WARM_ATTEMPTS} failed: ${err?.message ?? err}`);
    if (attempt < WARM_ATTEMPTS) {
      const backoffMs = 1000 * 2 ** (attempt - 1); // 1s, 2s, 4s
      console.error(`[warm-hf-cache] retrying in ${backoffMs}ms…`);
      await new Promise((r) => setTimeout(r, backoffMs));
    }
  }
}
if (lastErr) {
  if (OFFLINE) {
    // The offline proof: a baked cache we can't load is a broken image — fail loudly.
    console.error(`[warm-hf-cache] FATAL (VERIFY): baked cache missing/unloadable for ${MODEL_ID}: ${lastErr?.message ?? lastErr}`);
    process.exit(1);
  }
  console.error(
    `\n============================================================\n` +
    `[warm-hf-cache] ⚠️  WARN: could not pre-bake ${MODEL_ID} after ${WARM_ATTEMPTS} attempts\n` +
    `    (last error: ${lastErr?.message ?? lastErr}).\n` +
    `    This is almost always HuggingFace rate-limiting the CI egress IP (403), NOT a code fault.\n` +
    `    The image ships WITHOUT the warm cache; the bot will download the model on its FIRST\n` +
    `    Zoom/Teams meeting (a one-time few-second stall). Google Meet is unaffected.\n` +
    `    Not failing the build — pre-baking is an optimization, not a correctness gate.\n` +
    `============================================================\n`,
  );
  process.exit(0);
}

// Report cache contents + size so the build log shows the bake landed.
try {
  const du = execSync(`du -sh ${CACHE_DIR} 2>/dev/null || true`).toString().trim();
  console.log(`[warm-hf-cache] cache size: ${du}`);
  const tree = execSync(`find ${CACHE_DIR} -type f | sort 2>/dev/null || true`).toString().trim();
  console.log('[warm-hf-cache] cache files:\n' + tree);
} catch {
  // Reporting only — never fail the run on a du/find hiccup.
}

process.exit(0);
