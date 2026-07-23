#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILDER = path.join(HERE, "local-timeline.mjs");
const SAMPLE_RATE = 16_000;
const CONFIG_KEYS = [
  "OUT",
  "EVAL_CACHE",
  "SPEAKERS",
  "TURNS",
  "GAP_SEC",
  "OVERLAP_SEC",
  "OVERLAP",
  "LEADIN_SEC",
  "LEADIN",
  "TAILOUT_SEC",
  "TAILOUT",
  "STAGGER_SEC",
  "STAGGER",
  "SEED",
  "NOISE_FLOOR",
];

function syntheticWav(samples, amplitude, streamingHeader = false) {
  const dataBytes = samples * 2;
  const wav = Buffer.alloc(44 + dataBytes);
  wav.write("RIFF", 0, "ascii");
  wav.writeUInt32LE(36 + dataBytes, 4);
  wav.write("WAVE", 8, "ascii");
  wav.write("fmt ", 12, "ascii");
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(SAMPLE_RATE, 24);
  wav.writeUInt32LE(SAMPLE_RATE * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36, "ascii");
  wav.writeUInt32LE(dataBytes, 40);
  if (streamingHeader) {
    wav.writeUInt32LE(0x7fff_0024, 4);
    wav.writeUInt32LE(0x7fff_0000, 40);
  }
  for (let index = 0; index < samples; index += 1) {
    wav.writeInt16LE(index % 2 === 0 ? amplitude : -amplitude, 44 + index * 2);
  }
  return wav;
}

function cleanEnvironment(overrides) {
  const env = { ...process.env };
  for (const key of CONFIG_KEYS) delete env[key];
  return { ...env, ...overrides };
}

function run(args, env) {
  return spawnSync(process.execPath, [BUILDER, ...args], {
    env: cleanEnvironment(env),
    encoding: "utf8",
  });
}

function readJsonLines(file) {
  return fs
    .readFileSync(file, "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
}

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "vexa-local-timeline-test-"));
try {
  const cache = path.join(temporaryRoot, "cache");
  fs.mkdirSync(cache);
  for (const [key, name, baseAmplitude] of [
    ["A", "Anna", 5_000],
    ["B", "Boris", 6_000],
  ]) {
    const clips = [0, 1].map((index) => ({
      text: `${name} synthetic clip ${index}.`,
      b64: syntheticWav(
        800 + index * 160,
        baseAmplitude + index * 500,
        index === 0,
      ).toString("base64"),
      durSec: (800 + index * 160) / SAMPLE_RATE,
    }));
    fs.writeFileSync(path.join(cache, `${key}.json`), JSON.stringify(clips));
  }

  const first = path.join(temporaryRoot, "first");
  const common = {
    EVAL_CACHE: cache,
    SPEAKERS: "A,B",
    TURNS: "4",
    GAP_SEC: "0.02",
    LEADIN_SEC: "0.04",
    TAILOUT_SEC: "0.02",
    STAGGER_SEC: "0.01",
    SEED: "20260723",
    NOISE_FLOOR: "7",
  };
  const positive = run(["--out", first], common);
  assert.equal(positive.status, 0, positive.stderr);
  for (const file of [
    "Anna.wav",
    "Boris.wav",
    "truth.jsonl",
    "timeline.json",
    "verification.json",
  ]) {
    assert.equal(fs.existsSync(path.join(first, file)), true, `${file} was not created`);
  }

  const truth = readJsonLines(path.join(first, "truth.jsonl"));
  assert.deepEqual(
    truth.map(({ turn, key, speaker }) => ({ turn, key, speaker })),
    [
      { turn: 0, key: "A", speaker: "Anna" },
      { turn: 1, key: "B", speaker: "Boris" },
      { turn: 2, key: "A", speaker: "Anna" },
      { turn: 3, key: "B", speaker: "Boris" },
    ],
  );
  const verification = JSON.parse(
    fs.readFileSync(path.join(first, "verification.json"), "utf8"),
  );
  assert.equal(verification.equalDuration, true);
  assert.equal(verification.speechClipsWithEnergy, 4);
  assert.equal(verification.nonOverlap, true);
  assert.equal(verification.minimumGlobalGapMsByDirection["A→B"], 30);
  assert.equal(verification.minimumGlobalGapMsByDirection["B→A"], 10);
  assert.ok(verification.leadinRms.A > 0);
  assert.ok(verification.leadinRms.B > 0);
  assert.ok(verification.clipEnergy.every((row) => row.clipRms > 0.02));
  assert.ok(verification.clipEnergy.every((row) => row.trackRms > 0.02));

  const second = path.join(temporaryRoot, "second");
  const deterministic = run(["--out", second], common);
  assert.equal(deterministic.status, 0, deterministic.stderr);
  assert.equal(
    fs.readFileSync(path.join(first, "truth.jsonl"), "utf8"),
    fs.readFileSync(path.join(second, "truth.jsonl"), "utf8"),
    "same seed and cache must select the same clips",
  );

  const rejected = run([], {
    ...common,
    // Rebuild a previously green output so this case discriminates a stale
    // verification marker from a genuinely fail-closed fixture.
    OUT: first,
    GAP_SEC: "0.005",
    STAGGER_SEC: "0.02",
  });
  assert.notEqual(rejected.status, 0);
  assert.match(rejected.stderr, /becomes overlapping after simulated launch stagger/);
  assert.equal(fs.existsSync(path.join(first, "verification.json")), false);

  const overlap = path.join(temporaryRoot, "overlap");
  const allowed = run(
    [
      "--out",
      overlap,
      "--eval-cache",
      cache,
      "--speakers=A,B",
      "--turns",
      "4",
      "--overlap-sec=0.01",
      "--leadin-sec",
      "0.04",
      "--tailout-sec=0.02",
      "--stagger-sec",
      "0.01",
      "--seed=20260723",
      "--noise-floor",
      "7",
    ],
    {},
  );
  assert.equal(allowed.status, 0, allowed.stderr);
  const overlapVerification = JSON.parse(
    fs.readFileSync(path.join(overlap, "verification.json"), "utf8"),
  );
  assert.equal(overlapVerification.nonOverlap, false);
  assert.ok(overlapVerification.actualMinimumGlobalGapMs < 0);

  console.log(
    "local-timeline: 4 checks passed " +
      "(artifact shape, deterministic seed, stagger rejection, authored overlap)",
  );
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
}
