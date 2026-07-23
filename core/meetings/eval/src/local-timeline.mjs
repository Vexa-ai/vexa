#!/usr/bin/env node
/**
 * Build one full-length fake-microphone WAV per synthetic participant.
 *
 * Every track shares one authored timeline. A participant's turns contain the
 * selected cached TTS clip; all other samples contain a low, non-zero noise
 * floor so Chromium does not DTX-gate the fake microphone during lead-in.
 *
 * Environment variables and their equivalent CLI flags:
 *   OUT / --out
 *   EVAL_CACHE / --eval-cache
 *   SPEAKERS / --speakers
 *   TURNS / --turns
 *   GAP_SEC / --gap-sec OR OVERLAP_SEC / --overlap-sec
 *   LEADIN_SEC / --leadin-sec
 *   TAILOUT_SEC / --tailout-sec
 *   STAGGER_SEC / --stagger-sec
 *   SEED / --seed
 *   NOISE_FLOOR / --noise-floor
 *
 * The older speakers-skill spellings OVERLAP and LEADIN remain accepted.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SAMPLE_RATE = 16_000;
const PCM_BYTES = 2;
const MIN_SPEECH_RMS = 0.02;
const MAX_NOISE_FLOOR = 512;
// Deepgram's streaming WAV response cannot know its final length when it emits
// the header, so every cached corpus clip uses this deliberate placeholder.
const STREAMING_DATA_SIZE = 0x7fff_0000;

// Keep the ordinary display names used by speech_fixture.py. Cache keys, not
// names, are the stable input to this builder.
const DISPLAY_NAMES = Object.freeze({
  A: "Anna",
  B: "Boris",
  C: "Galina",
  D: "Dmitry",
  E: "Elena",
  F: "Fyodor",
  G: "Grigory",
  H: "Hanna",
  V: "Vera",
});

const ARG_FIELDS = Object.freeze({
  out: "out",
  "eval-cache": "evalCache",
  speakers: "speakers",
  turns: "turns",
  "gap-sec": "gapSec",
  "overlap-sec": "overlapSec",
  "leadin-sec": "leadinSec",
  "tailout-sec": "tailoutSec",
  "stagger-sec": "staggerSec",
  seed: "seed",
  "noise-floor": "noiseFloor",
});

function usage() {
  return `Usage:
  OUT=<dir> SPEAKERS=A,B TURNS=14 GAP_SEC=4 STAGGER_SEC=3 \\
    node core/meetings/eval/src/local-timeline.mjs

Equivalent CLI:
  node core/meetings/eval/src/local-timeline.mjs \\
    --out <dir> --speakers A,B --turns 14 --gap-sec 4 --stagger-sec 3

Choose exactly one of GAP_SEC/--gap-sec and OVERLAP_SEC/--overlap-sec.
Defaults: EVAL_CACHE=~/vexa-test-rig/cache, SPEAKERS=A,B, TURNS=14,
GAP_SEC=1, LEADIN_SEC=30, TAILOUT_SEC=8, STAGGER_SEC=0, SEED=7,
NOISE_FLOOR=25 (signed PCM amplitude).`;
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--help" || token === "-h") return { help: true };
    if (!token.startsWith("--")) {
      throw new Error(`unexpected positional argument: ${token}`);
    }

    const equals = token.indexOf("=");
    const name = token.slice(2, equals === -1 ? undefined : equals);
    const field = ARG_FIELDS[name];
    if (!field) throw new Error(`unknown option --${name}`);
    if (Object.hasOwn(values, field)) throw new Error(`--${name} specified more than once`);

    let value;
    if (equals !== -1) {
      value = token.slice(equals + 1);
    } else {
      value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new Error(`--${name} requires a value`);
      }
      index += 1;
    }
    values[field] = value;
  }
  return values;
}

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== "");
}

function expandHome(value) {
  if (value === "~") return os.homedir();
  if (value.startsWith("~/")) return path.join(os.homedir(), value.slice(2));
  return value;
}

function finiteNumber(name, raw, { min = -Infinity, max = Infinity } = {}) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${name} must be a finite number in [${min}, ${max}], got ${raw}`);
  }
  return value;
}

function integer(name, raw, { min, max }) {
  const value = finiteNumber(name, raw, { min, max });
  if (!Number.isInteger(value)) throw new Error(`${name} must be an integer, got ${raw}`);
  return value;
}

function parseConfig(argv, env) {
  const cli = parseArgs(argv);
  if (cli.help) return cli;

  const outRaw = firstDefined(cli.out, env.OUT);
  if (!outRaw) throw new Error(`OUT/--out is required\n\n${usage()}`);

  const speakerRaw = firstDefined(cli.speakers, env.SPEAKERS, "A,B");
  const speakers = speakerRaw
    .split(",")
    .map((key) => key.trim().toUpperCase())
    .filter(Boolean);
  if (speakers.length === 0) throw new Error("SPEAKERS must contain at least one cache key");
  if (new Set(speakers).size !== speakers.length) {
    throw new Error(`SPEAKERS contains duplicate keys: ${speakerRaw}`);
  }
  for (const key of speakers) {
    if (!DISPLAY_NAMES[key]) {
      throw new Error(
        `unknown speaker cache key ${key}; expected one of ${Object.keys(DISPLAY_NAMES).join(",")}`,
      );
    }
  }

  const turns = integer("TURNS", firstDefined(cli.turns, env.TURNS, 14), {
    min: speakers.length,
    max: 10_000,
  });
  const seed = integer("SEED", firstDefined(cli.seed, env.SEED, 7), {
    min: 0,
    max: 0xffff_ffff,
  });
  const noiseFloor = integer(
    "NOISE_FLOOR",
    firstDefined(cli.noiseFloor, env.NOISE_FLOOR, 25),
    { min: 1, max: MAX_NOISE_FLOOR },
  );

  const gapRaw = firstDefined(cli.gapSec, env.GAP_SEC);
  const overlapRaw = firstDefined(cli.overlapSec, env.OVERLAP_SEC, env.OVERLAP);
  if (gapRaw !== undefined && overlapRaw !== undefined) {
    throw new Error("choose GAP_SEC/--gap-sec OR OVERLAP_SEC/--overlap-sec, not both");
  }
  const timingMode = overlapRaw === undefined ? "gap" : "overlap";
  const gapSec =
    timingMode === "gap" ? finiteNumber("GAP_SEC", gapRaw ?? 1, { min: 0 }) : 0;
  const overlapSec =
    timingMode === "overlap"
      ? finiteNumber("OVERLAP_SEC", overlapRaw, { min: 0 })
      : 0;

  const leadinSec = finiteNumber(
    "LEADIN_SEC",
    firstDefined(cli.leadinSec, env.LEADIN_SEC, env.LEADIN, 30),
    { min: 1 / SAMPLE_RATE },
  );
  const tailoutSec = finiteNumber(
    "TAILOUT_SEC",
    firstDefined(cli.tailoutSec, env.TAILOUT_SEC, env.TAILOUT, 8),
    { min: 0 },
  );
  const staggerSec = finiteNumber(
    "STAGGER_SEC",
    firstDefined(cli.staggerSec, env.STAGGER_SEC, env.STAGGER, 0),
    { min: 0 },
  );

  return {
    out: path.resolve(expandHome(outRaw)),
    evalCache: path.resolve(
      expandHome(firstDefined(cli.evalCache, env.EVAL_CACHE, "~/vexa-test-rig/cache")),
    ),
    speakers,
    turns,
    timingMode,
    gapSec,
    overlapSec,
    leadinSec,
    tailoutSec,
    staggerSec,
    seed,
    noiseFloor,
  };
}

function secondsToSamples(seconds) {
  return Math.round(seconds * SAMPLE_RATE);
}

function samplesToSeconds(samples) {
  return Number((samples / SAMPLE_RATE).toFixed(6));
}

function samplesToMilliseconds(samples) {
  return Number(((samples * 1000) / SAMPLE_RATE).toFixed(3));
}

function parseWav(buffer, source) {
  if (
    buffer.length < 12 ||
    buffer.toString("ascii", 0, 4) !== "RIFF" ||
    buffer.toString("ascii", 8, 12) !== "WAVE"
  ) {
    throw new Error(`${source}: expected a RIFF/WAVE file`);
  }

  let format;
  let data;
  let offset = 12;
  while (offset + 8 <= buffer.length) {
    const id = buffer.toString("ascii", offset, offset + 4);
    const size = buffer.readUInt32LE(offset + 4);
    const start = offset + 8;
    let end = start + size;
    if (end > buffer.length) {
      if (id === "data" && size === STREAMING_DATA_SIZE) {
        end = buffer.length;
      } else {
        throw new Error(`${source}: truncated ${id} chunk`);
      }
    }

    if (id === "fmt ") {
      if (size < 16) throw new Error(`${source}: fmt chunk is only ${size} bytes`);
      format = {
        audioFormat: buffer.readUInt16LE(start),
        channels: buffer.readUInt16LE(start + 2),
        sampleRate: buffer.readUInt32LE(start + 4),
        bitsPerSample: buffer.readUInt16LE(start + 14),
      };
    } else if (id === "data" && data === undefined) {
      data = buffer.subarray(start, end);
      if (data.length % PCM_BYTES !== 0) {
        throw new Error(`${source}: PCM data has an odd byte count`);
      }
    }
    offset = end + (end - start) % 2;
  }

  if (!format || data === undefined) throw new Error(`${source}: missing fmt or data chunk`);
  if (
    format.audioFormat !== 1 ||
    format.channels !== 1 ||
    format.sampleRate !== SAMPLE_RATE ||
    format.bitsPerSample !== 16
  ) {
    throw new Error(
      `${source}: expected PCM16 ${SAMPLE_RATE}Hz mono, got ` +
        `${JSON.stringify(format)}`,
    );
  }
  if (data.length === 0) throw new Error(`${source}: audio data is empty`);

  return { ...format, data, samples: data.length / PCM_BYTES };
}

function rms(buffer, startSample = 0, endSample = buffer.length / PCM_BYTES) {
  if (endSample <= startSample) return 0;
  let sum = 0;
  for (let index = startSample; index < endSample; index += 1) {
    const value = buffer.readInt16LE(index * PCM_BYTES) / 32768;
    sum += value * value;
  }
  return Math.sqrt(sum / (endSample - startSample));
}

function decodeClip(entry, source) {
  if (!entry || typeof entry !== "object") throw new Error(`${source}: clip is not an object`);
  if (typeof entry.text !== "string" || entry.text.trim() === "") {
    throw new Error(`${source}: clip text is empty`);
  }
  if (typeof entry.b64 !== "string" || entry.b64 === "") {
    throw new Error(`${source}: clip b64 WAV is empty`);
  }
  const wav = parseWav(Buffer.from(entry.b64, "base64"), source);
  const clipRms = rms(wav.data);
  if (!(clipRms > MIN_SPEECH_RMS)) {
    throw new Error(
      `${source}: speech RMS ${clipRms.toFixed(6)} must exceed ${MIN_SPEECH_RMS}`,
    );
  }
  return { text: entry.text.trim(), wav, clipRms };
}

function loadPools(config) {
  const pools = new Map();
  for (const key of config.speakers) {
    const file = path.join(config.evalCache, `${key}.json`);
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch (error) {
      throw new Error(`${file}: cannot read cached TTS clips (${error.message})`);
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      throw new Error(`${file}: expected a non-empty JSON array of cached TTS clips`);
    }
    pools.set(key, { file, entries: parsed });
  }
  return pools;
}

function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 0x1_0000_0000;
  };
}

function shuffledIndexes(length, random) {
  const indexes = Array.from({ length }, (_, index) => index);
  for (let index = indexes.length - 1; index > 0; index -= 1) {
    const other = Math.floor(random() * (index + 1));
    [indexes[index], indexes[other]] = [indexes[other], indexes[index]];
  }
  return indexes;
}

function selectTurns(config, pools) {
  const random = mulberry32(config.seed);
  const selectors = new Map(
    config.speakers.map((key) => [
      key,
      { indexes: shuffledIndexes(pools.get(key).entries.length, random), cursor: 0 },
    ]),
  );
  const decoded = new Map();
  const selected = [];

  for (let turn = 0; turn < config.turns; turn += 1) {
    const key = config.speakers[turn % config.speakers.length];
    const selector = selectors.get(key);
    if (selector.cursor === selector.indexes.length) {
      selector.indexes = shuffledIndexes(pools.get(key).entries.length, random);
      selector.cursor = 0;
    }
    const clipIndex = selector.indexes[selector.cursor];
    selector.cursor += 1;

    const identity = `${key}:${clipIndex}`;
    let clip = decoded.get(identity);
    if (!clip) {
      const pool = pools.get(key);
      clip = decodeClip(pool.entries[clipIndex], `${pool.file}[${clipIndex}]`);
      decoded.set(identity, clip);
    }
    selected.push({
      turn,
      key,
      speaker: DISPLAY_NAMES[key],
      clipIndex,
      clip,
    });
  }
  return selected;
}

function authorTimeline(config, selected) {
  const gapSamples = secondsToSamples(config.gapSec);
  const overlapSamples = secondsToSamples(config.overlapSec);
  const separationSamples =
    config.timingMode === "gap" ? gapSamples : -overlapSamples;
  const leadinSamples = secondsToSamples(config.leadinSec);
  const tailoutSamples = secondsToSamples(config.tailoutSec);
  const staggerSamples = secondsToSamples(config.staggerSec);
  const launchOffsets = Object.fromEntries(
    config.speakers.map((key, index) => [key, index * staggerSamples]),
  );

  let cursor = leadinSamples;
  const turns = selected.map((selection) => {
    const startSample = cursor;
    const endSample = startSample + selection.clip.wav.samples;
    cursor = endSample + separationSamples;
    return { ...selection, startSample, endSample };
  });

  for (const key of config.speakers) {
    let priorEnd = -Infinity;
    for (const turn of turns.filter((candidate) => candidate.key === key)) {
      if (turn.startSample < priorEnd) {
        throw new Error(
          `turn ${turn.turn} overlaps ${DISPLAY_NAMES[key]}'s own prior turn; ` +
            "reduce OVERLAP_SEC or add speakers",
        );
      }
      priorEnd = turn.endSample;
    }
  }

  const globalGaps = [];
  for (let index = 1; index < turns.length; index += 1) {
    const previous = turns[index - 1];
    const current = turns[index];
    const authoredGapSamples = current.startSample - previous.endSample;
    const globalGapSamples =
      current.startSample +
      launchOffsets[current.key] -
      (previous.endSample + launchOffsets[previous.key]);
    globalGaps.push({
      from: previous.key,
      to: current.key,
      authoredGapSamples,
      globalGapSamples,
    });
  }

  const nonOverlap = separationSamples >= 0;
  const negative = globalGaps.find((gap) => gap.globalGapSamples < 0);
  if (nonOverlap && negative) {
    throw new Error(
      `non-overlap fixture becomes overlapping after simulated launch stagger: ` +
        `${negative.from}→${negative.to} global gap ` +
        `${samplesToSeconds(negative.globalGapSamples)}s; increase GAP_SEC or reduce STAGGER_SEC`,
    );
  }

  const finalEnd = Math.max(...turns.map((turn) => turn.endSample));
  const durationSamples = finalEnd + tailoutSamples;
  return {
    turns,
    globalGaps,
    launchOffsets,
    nonOverlap,
    leadinSamples,
    tailoutSamples,
    separationSamples,
    durationSamples,
  };
}

function hashKey(seed, key) {
  let value = seed >>> 0;
  for (const character of key) {
    value = Math.imul(value ^ character.codePointAt(0), 0x45d9f3b) >>> 0;
  }
  return value || 0x9e3779b9;
}

function buildTrack(config, authored, key) {
  const samples = new Int16Array(authored.durationSamples);
  let state = hashKey(config.seed, key);
  for (let index = 0; index < samples.length; index += 1) {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
    const magnitude = 1 + ((state >>> 1) % config.noiseFloor);
    samples[index] = state & 1 ? magnitude : -magnitude;
  }

  for (const turn of authored.turns.filter((candidate) => candidate.key === key)) {
    const clip = turn.clip.wav.data;
    for (let index = 0; index < turn.clip.wav.samples; index += 1) {
      const mixed = samples[turn.startSample + index] + clip.readInt16LE(index * PCM_BYTES);
      samples[turn.startSample + index] = Math.max(-32768, Math.min(32767, mixed));
    }
  }
  return samples;
}

function encodeWav(samples) {
  const dataBytes = samples.byteLength;
  const wav = Buffer.alloc(44 + dataBytes);
  wav.write("RIFF", 0, "ascii");
  wav.writeUInt32LE(36 + dataBytes, 4);
  wav.write("WAVE", 8, "ascii");
  wav.write("fmt ", 12, "ascii");
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(SAMPLE_RATE, 24);
  wav.writeUInt32LE(SAMPLE_RATE * PCM_BYTES, 28);
  wav.writeUInt16LE(PCM_BYTES, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36, "ascii");
  wav.writeUInt32LE(dataBytes, 40);
  Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength).copy(wav, 44);
  return wav;
}

function publicTruth(authored) {
  return authored.turns.map((turn) => ({
    turn: turn.turn,
    key: turn.key,
    speaker: turn.speaker,
    startSec: samplesToSeconds(turn.startSample),
    endSec: samplesToSeconds(turn.endSample),
    durSec: samplesToSeconds(turn.clip.wav.samples),
    text: turn.clip.text,
    clipIndex: turn.clipIndex,
  }));
}

function minimumsByDirection(globalGaps) {
  const grouped = new Map();
  for (const gap of globalGaps) {
    const direction = `${gap.from}→${gap.to}`;
    const milliseconds = samplesToMilliseconds(gap.globalGapSamples);
    grouped.set(direction, Math.min(grouped.get(direction) ?? Infinity, milliseconds));
  }
  return Object.fromEntries([...grouped.entries()].sort(([left], [right]) => left.localeCompare(right)));
}

function writeJson(file, value) {
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function build(config) {
  const outputPaths = {
    truth: path.join(config.out, "truth.jsonl"),
    timeline: path.join(config.out, "timeline.json"),
    verification: path.join(config.out, "verification.json"),
  };

  fs.mkdirSync(config.out, { recursive: true });
  // verification.json is the success marker. Never leave an old green marker
  // beside outputs from a failed rebuild.
  fs.rmSync(outputPaths.verification, { force: true });

  const pools = loadPools(config);
  const selected = selectTurns(config, pools);
  const authored = authorTimeline(config, selected);
  const truth = publicTruth(authored);
  const durationSec = samplesToSeconds(authored.durationSamples);
  const stage = fs.mkdtempSync(path.join(config.out, ".local-timeline-"));

  try {
    const leadinRms = {};
    const trackSamples = [];
    const trackEnergyByTurn = new Map();
    const stagedWavs = new Map();

    for (const key of config.speakers) {
      const name = DISPLAY_NAMES[key];
      const staged = path.join(stage, `${name}.wav`);
      fs.writeFileSync(staged, encodeWav(buildTrack(config, authored, key)));
      const wav = parseWav(fs.readFileSync(staged), staged);
      if (wav.samples !== authored.durationSamples) {
        throw new Error(
          `${name}: output has ${wav.samples} samples, expected ${authored.durationSamples}`,
        );
      }
      leadinRms[key] = rms(wav.data, 0, authored.leadinSamples);
      if (!(leadinRms[key] > 0.00001)) {
        throw new Error(`${name}: lead-in noise floor is digitally silent`);
      }
      trackSamples.push(wav.samples);
      for (const turn of authored.turns.filter((candidate) => candidate.key === key)) {
        const trackRms = rms(wav.data, turn.startSample, turn.endSample);
        if (!(trackRms > MIN_SPEECH_RMS)) {
          throw new Error(
            `turn ${turn.turn}: output speech RMS ${trackRms.toFixed(6)} ` +
              `must exceed ${MIN_SPEECH_RMS}`,
          );
        }
        trackEnergyByTurn.set(turn.turn, trackRms);
      }
      stagedWavs.set(key, staged);
    }

    const clipEnergy = authored.turns.map((turn) => {
      const trackRms = trackEnergyByTurn.get(turn.turn);
      return {
        turn: turn.turn,
        key: turn.key,
        clipRms: Number(turn.clip.clipRms.toFixed(8)),
        trackRms: Number(trackRms.toFixed(8)),
      };
    });

    const launchOffsetsMs = Object.fromEntries(
      Object.entries(authored.launchOffsets).map(([key, samples]) => [
        key,
        samplesToMilliseconds(samples),
      ]),
    );
    const globalGaps = authored.globalGaps.map((gap, index) => ({
      afterTurn: index,
      beforeTurn: index + 1,
      from: gap.from,
      to: gap.to,
      authoredGapMs: samplesToMilliseconds(gap.authoredGapSamples),
      globalGapMs: samplesToMilliseconds(gap.globalGapSamples),
    }));
    const actualMinimumGlobalGapMs =
      globalGaps.length === 0
        ? null
        : Math.min(...globalGaps.map((gap) => gap.globalGapMs));

    const speakers = config.speakers.map((key) => {
      const speakerTurns = truth.filter((turn) => turn.key === key);
      return {
        key,
        name: DISPLAY_NAMES[key],
        wav: path.join(config.out, `${DISPLAY_NAMES[key]}.wav`),
        turns: speakerTurns.length,
        speechSec: Number(
          speakerTurns.reduce((sum, turn) => sum + turn.durSec, 0).toFixed(6),
        ),
      };
    });
    const timeline = {
      v: 1,
      generatedAt: new Date().toISOString(),
      source: {
        cache: config.evalCache,
        pools: Object.fromEntries(
          config.speakers.map((key) => [key, path.join(config.evalCache, `${key}.json`)]),
        ),
      },
      seed: config.seed,
      dials: {
        speakers: config.speakers,
        turns: config.turns,
        overlapSec: config.overlapSec,
        gapSec: config.gapSec,
        leadinSec: samplesToSeconds(authored.leadinSamples),
        tailoutSec: samplesToSeconds(authored.tailoutSamples),
        launchStaggerSec: config.staggerSec,
        actualMinimumGlobalGapSec:
          actualMinimumGlobalGapMs === null ? null : actualMinimumGlobalGapMs / 1000,
        noiseFloor: config.noiseFloor,
      },
      sampleRate: SAMPLE_RATE,
      durationSec,
      truth: outputPaths.truth,
      speakers,
    };
    const verification = {
      format: `PCM16 ${SAMPLE_RATE}Hz mono`,
      samplesPerTrack: authored.durationSamples,
      durationSec,
      equalDuration: new Set(trackSamples).size === 1,
      leadinRms: Object.fromEntries(
        Object.entries(leadinRms).map(([key, value]) => [key, Number(value.toFixed(8))]),
      ),
      speechClipsWithEnergy: clipEnergy.length,
      minimumSpeechRms: MIN_SPEECH_RMS,
      clipEnergy,
      nonOverlap: authored.nonOverlap,
      actualMinimumGlobalGapMs,
      minimumGlobalGapMsByDirection: minimumsByDirection(authored.globalGaps),
      simulatedLaunchOffsetsMs: launchOffsetsMs,
      globalGaps,
    };
    if (!verification.equalDuration) throw new Error("speaker WAV durations differ");

    fs.writeFileSync(
      path.join(stage, "truth.jsonl"),
      `${truth.map((row) => JSON.stringify(row)).join("\n")}\n`,
    );
    writeJson(path.join(stage, "timeline.json"), timeline);
    writeJson(path.join(stage, "verification.json"), verification);

    for (const speaker of speakers) {
      fs.renameSync(stagedWavs.get(speaker.key), speaker.wav);
    }
    fs.renameSync(path.join(stage, "truth.jsonl"), outputPaths.truth);
    fs.renameSync(path.join(stage, "timeline.json"), outputPaths.timeline);
    // Publish the success marker last.
    fs.renameSync(path.join(stage, "verification.json"), outputPaths.verification);

    console.log(
      `Built ${truth.length} turns over ${durationSec.toFixed(3)}s in ${config.out}`,
    );
    console.log(
      `Tracks: ${speakers.map((speaker) => `${speaker.name}=${speaker.turns}`).join(", ")}; ` +
        `minimum global gap=${
          actualMinimumGlobalGapMs === null ? "n/a" : `${actualMinimumGlobalGapMs}ms`
        }`,
    );
    console.log(`Verified: ${outputPaths.verification}`);
    return { timeline, truth, verification };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

function main() {
  try {
    const config = parseConfig(process.argv.slice(2), process.env);
    if (config.help) {
      console.log(usage());
      return;
    }
    build(config);
  } catch (error) {
    console.error(`[local-timeline] ${error.message}`);
    process.exitCode = 1;
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) main();
