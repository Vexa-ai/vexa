#!/usr/bin/env node
/**
 * Pre-download the two ONNX models used by the MS Teams diarization
 * cutover so the runtime container doesn't hit Hugging Face on first
 * bot start.
 *
 * Run during the Dockerfile's `ts-builder` stage AFTER `npm install`
 * so the resulting cache directory gets COPY'd into the runtime
 * image alongside node_modules.
 *
 * Models:
 *   - onnx-community/pyannote-segmentation-3.0    (~6.6 MB, per-frame segmentation)
 *   - onnx-community/wespeaker-voxceleb-resnet34-LM  (~25 MB, embedding)
 *
 * Cache layout: transformers.js writes to
 *   node_modules/@huggingface/transformers/.cache/onnx-community/<model>/...
 * which becomes part of the layer COPY'd to the runtime stage.
 *
 * Pack: msteams-diarization-cutover (#394).
 */
const { AutoModel, AutoProcessor, env } = require('@huggingface/transformers');

// Force network downloads — no local-model lookup at build time.
env.allowLocalModels = true;
env.allowRemoteModels = true;

const MODELS = [
  { id: 'onnx-community/pyannote-segmentation-3.0', opts: {} },
  { id: 'onnx-community/wespeaker-voxceleb-resnet34-LM', opts: { dtype: 'fp32' } },
];

async function bake(modelId, modelOpts) {
  const t0 = Date.now();
  console.log(`[bake] ${modelId} — downloading processor...`);
  await AutoProcessor.from_pretrained(modelId);
  console.log(`[bake] ${modelId} — downloading model...`);
  await AutoModel.from_pretrained(modelId, modelOpts);
  console.log(`[bake] ${modelId} — done (${Date.now() - t0} ms)`);
}

(async () => {
  for (const m of MODELS) {
    try {
      await bake(m.id, m.opts);
    } catch (err) {
      console.error(`[bake] FAILED for ${m.id}: ${err && err.message ? err.message : err}`);
      process.exit(1);
    }
  }
  console.log('[bake] all models cached');
})();
