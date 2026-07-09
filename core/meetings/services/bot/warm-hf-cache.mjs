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
 *   - VERIFY (VEXA_HF_OFFLINE=1, runtime, `docker run --network none`):
 *       env.allowRemoteModels = false → MUST load from $VEXA_HF_CACHE only.
 *       Any network fetch attempt fails → non-zero exit → bake is broken.
 *
 * Exit 0 = model loaded; non-zero = failure (so it gates the docker build and
 * doubles as the offline proof).
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
try {
  const model = await AutoModel.from_pretrained(MODEL_ID, { device: 'cpu' });
  const processor = await AutoProcessor.from_pretrained(MODEL_ID);
  if (!model || !processor) throw new Error('from_pretrained returned empty');
  console.log(`[warm-hf-cache] OK: model + processor loaded in ${Date.now() - t0}ms`);
} catch (err) {
  console.error(`[warm-hf-cache] FAILED to load ${MODEL_ID}: ${err?.message ?? err}`);
  process.exit(1);
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
