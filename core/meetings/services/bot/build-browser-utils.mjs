#!/usr/bin/env node
/**
 * Build the page-side capture bundle → dist/browser-utils.global.js.
 *
 * This is the v0.12 equivalent of production's
 *   services/vexa-bot/core/build-browser-utils.js
 * but driven by esbuild instead of a hand-rolled CommonJS-wrapper, because the
 * v0.12 capture bricks are ESM (NodeNext) and import each other by relative path.
 *
 * WHY a bundle (not a bot dependency): the page-side capture module
 * (@vexa/gmeet-capture / @vexa/mixed-capture-core / @vexa/capture-codec) is NOT a
 * bot import (gate:isolation) — it is a BROWSER bundle loaded into the meeting page
 * at runtime. capture-bridge.ts injects this file via addInitScript so that
 * `window.VexaBrowserUtils.*` is present on every navigation; the Node side imports
 * nothing from those packages (PCM crosses the Playwright boundary as plain
 * `(speakerIndex, number[])` over page.exposeFunction).
 *
 * THE CONTRACT (what capture-bridge.ts calls on window.VexaBrowserUtils):
 *   • createGmeetCapture({ log, onAudio })        — gmeet lane per-channel PCM
 *   • createGmeetSpeakers({ log })                — gmeet lane glow → litNames()
 *   • createMixedAudioCapture(stream, onPcm)      — mixed lane (zoom/teams)
 * We additionally expose the rest of the gmeet capture surface
 * (GmeetChannelBinder, createPcmCaptureNode, createGmeetCaptureV1, pickBoundName,
 * installRemoteAudioHook) so the global mirrors production's shape and the same
 * module the extension runs.
 *
 * esbuild resolves the @vexa/* entries by absolute path (computed below from the
 * workspace layout), so this build does NOT depend on the capture packages being
 * linked into the bot's node_modules (they are not — see gate:isolation).
 */
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// bot dir = meetings/services/bot ; modules live at meetings/modules/*
const MODULES = path.resolve(__dirname, '..', '..', 'modules');

/** Resolve a workspace capture brick's built ESM entry (dist/index.js). */
function moduleEntry(pkgDir) {
  const entry = path.join(MODULES, pkgDir, 'dist', 'index.js');
  if (!fs.existsSync(entry)) {
    throw new Error(
      `[build-browser-utils] missing built entry: ${entry}\n` +
      `  Build the capture bricks first (pnpm --filter @vexa/${pkgDir} build).`,
    );
  }
  return entry;
}

const GMEET = moduleEntry('gmeet-capture');       // @vexa/gmeet-capture
const MIXED = moduleEntry('mixed-capture-core');  // @vexa/mixed-capture-core
const RECORD = moduleEntry('record-chunker');     // @vexa/record-chunker (MediaRecorder → recording.v1)
const JITSI = moduleEntry('jitsi-capture');       // @vexa/jitsi-capture (dominant-speaker hints + chat)

// In-memory entry: import the bricks and hang them on window.VexaBrowserUtils with
// the EXACT names capture-bridge.ts reaches for. esbuild bundles the relative
// imports each brick makes (pcm-capture, gmeet-speakers, …) into one IIFE.
const entryContents = `
import {
  createGmeetCapture,
  createGmeetSpeakers,
  createGmeetCaptureV1,
  pickBoundName,
  GmeetChannelBinder,
  createPcmCaptureNode,
} from ${JSON.stringify(GMEET)};
import {
  createMixedAudioCapture,
  installRemoteAudioHook,
} from ${JSON.stringify(MIXED)};
import {
  createRecordingTap,
} from ${JSON.stringify(RECORD)};
import {
  createJitsiSpeakers,
  createJitsiChat,
  sendJitsiChatMessage,
} from ${JSON.stringify(JITSI)};

const VexaBrowserUtils = {
  // ── gmeet lane (per-participant capture + glow attribution) ──
  createGmeetCapture,        // capture-bridge.ts: w.VexaBrowserUtils.createGmeetCapture
  createGmeetSpeakers,       // capture-bridge.ts: w.VexaBrowserUtils.createGmeetSpeakers (litNames())
  createGmeetCaptureV1,      // the v1 producer (source-bound glow name)
  pickBoundName,
  GmeetChannelBinder,
  createPcmCaptureNode,
  // ── mixed lane (zoom/teams single combined stream) ──
  createMixedAudioCapture,   // capture-bridge.ts: w.VexaBrowserUtils.createMixedAudioCapture
  installRemoteAudioHook,
  // ── recording (all platforms): MediaRecorder → recording.v1 chunks ──
  createRecordingTap,        // capture-bridge.ts: w.VexaBrowserUtils.createRecordingTap
  // ── jitsi lane (dominant-speaker naming hints + chat over the app's own state) ──
  createJitsiSpeakers,       // capture-bridge.ts: w.VexaBrowserUtils.createJitsiSpeakers
  createJitsiChat,           // capture-bridge.ts: w.VexaBrowserUtils.createJitsiChat
  sendJitsiChatMessage,
};

(globalThis).VexaBrowserUtils = VexaBrowserUtils;
if (typeof window !== 'undefined') window.VexaBrowserUtils = VexaBrowserUtils;

// Parity with production's bundle: a no-op leave hook the page may call.
const performLeaveAction = function (reason) {
  if (typeof window !== 'undefined' && window.logBot) {
    window.logBot('Platform-specific leave action triggered: ' + String(reason));
  }
};
(globalThis).performLeaveAction = performLeaveAction;
if (typeof window !== 'undefined') window.performLeaveAction = performLeaveAction;

try {
  console.log('Vexa Browser Utils loaded successfully:', Object.keys(VexaBrowserUtils));
} catch (e) { /* console may be absent in some realms */ }
`;

const OUT = path.join(__dirname, 'dist', 'browser-utils.global.js');
fs.mkdirSync(path.dirname(OUT), { recursive: true });

await build({
  stdin: {
    contents: entryContents,
    resolveDir: __dirname,
    loader: 'js',
    sourcefile: 'browser-utils-entry.js',
  },
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: ['chrome120'],
  outfile: OUT,
  legalComments: 'none',
  logLevel: 'info',
});

const bytes = fs.statSync(OUT).size;
console.log(`✅ Browser utilities bundle created: ${OUT} (${bytes} bytes)`);
console.log('📦 window.VexaBrowserUtils exposes:');
console.log('  - createGmeetCapture / createGmeetSpeakers / createGmeetCaptureV1 / pickBoundName');
console.log('  - GmeetChannelBinder / createPcmCaptureNode');
console.log('  - createMixedAudioCapture / installRemoteAudioHook');
console.log('  - window.performLeaveAction');
