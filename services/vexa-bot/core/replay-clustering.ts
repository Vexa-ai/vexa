/**
 * Fast clustering replay (pack-msteams-diarization-cutover #394 RnD).
 *
 * Reads cached per-utterance embeddings (from eval-diarizer.ts DIAR_DUMP_EMB)
 * and replays ONLY the online-clustering assignment loop — exactly as
 * OnnxLocalDiarizer.commitUtterance does — for a given clustering config.
 * No pyannote, no wespeaker, no model load: runs in milliseconds, so a full
 * threshold sweep over a cached clip is sub-second.
 *
 * Faithful to commitUtterance: canSeedNew (cached) + cooldown(allowNewCluster)
 * + assignWithSeedGate + mergeClose(0.30) + labelRewrites resolution.
 *
 * Usage:
 *   npx tsx replay-clustering.ts --emb X.emb.json --out X.commits.json \
 *       --config '{"newSpeakerThreshold":0.45,"veryFarThreshold":0.65,"newClusterCooldownMs":4000}'
 */
import { OnlineSpeakerClustering } from './src/services/diarization/online-clustering';
import * as fs from 'fs';

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def;
}

function replayOne(records: any[], durationS: number, config: any, outPath: string) {
  const clustering = new OnlineSpeakerClustering({
    newSpeakerThreshold: config.newSpeakerThreshold ?? 0.45,
    veryFarThreshold: config.veryFarThreshold ?? 0.65,
    maxSpeakers: config.maxSpeakers,
  });
  const cooldownMs = config.newClusterCooldownMs ?? 4000;
  const mergeThreshold = config.mergeThreshold ?? 0.3;

  let lastNewClusterTs = -Infinity;
  let lastLabelId: string | null = null;
  const labelRewrites = new Map<string, string>();
  const commits: any[] = [];

  for (const r of records) {
    const emb = Float32Array.from(r.emb);
    const allowNewCluster = r.tEndMs - lastNewClusterTs >= cooldownMs;
    const assignment = clustering.assignWithSeedGate(emb, r.canSeedNew, allowNewCluster, lastLabelId, 0);
    if (assignment.isNew) lastNewClusterTs = r.tEndMs;

    const merges = clustering.mergeClose(mergeThreshold);
    for (const [oldId, keptId] of merges) {
      let target = keptId;
      while (labelRewrites.has(target)) target = labelRewrites.get(target)!;
      labelRewrites.set(oldId, target);
    }
    let finalSpeakerId = assignment.speakerId;
    while (labelRewrites.has(finalSpeakerId)) finalSpeakerId = labelRewrites.get(finalSpeakerId)!;

    commits.push({
      speakerId: finalSpeakerId,
      startS: +(r.tStartMs / 1000).toFixed(3),
      endS: +(r.tEndMs / 1000).toFixed(3),
      isNew: assignment.isNew && finalSpeakerId === assignment.speakerId,
      centroidDist: Number.isFinite(assignment.distance) ? +assignment.distance.toFixed(3) : null,
      dbSize: clustering.size(),
    });
    lastLabelId = finalSpeakerId;
  }

  const clusters = new Set(commits.map((c) => c.speakerId));
  fs.writeFileSync(
    outPath,
    JSON.stringify({ config, durationS, wallS: 0, nCommits: commits.length, nClusters: clusters.size, clusters: [...clusters], commits }, null, 2),
  );
  return clusters.size;
}

function main() {
  const embPath = arg('emb')!;
  const { durationS, records } = JSON.parse(fs.readFileSync(embPath, 'utf8'));
  const configsPath = arg('configs');
  if (configsPath) {
    // batch mode: configs.json = [{name, config, out}], one tsx startup for all
    const jobs = JSON.parse(fs.readFileSync(configsPath, 'utf8'));
    const t0 = Date.now();
    for (const j of jobs) {
      const n = replayOne(records, durationS, j.config, j.out);
      console.log(`[replay] ${j.name}: ${n} clusters`);
    }
    console.log(`[replay] ${jobs.length} configs replayed in ${Date.now() - t0}ms over ${records.length} cached utterances`);
  } else {
    const n = replayOne(records, durationS, JSON.parse(arg('config', '{}')!), arg('out')!);
    console.log(`[replay] ${n} clusters → ${arg('out')}`);
  }
}

main();
