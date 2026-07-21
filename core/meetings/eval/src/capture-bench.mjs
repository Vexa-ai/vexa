#!/usr/bin/env node
// capture-bench — reproduce capture loss with no meeting, no bot, and no human.
//
// A 65% capture duty cycle has two candidate mechanisms that leave IDENTICAL evidence in a
// recording, because both simply omit a frame:
//
//   the silence gate      mixed-audio.ts refuses a 256ms buffer whose peak is <= 0.005
//   the ScriptProcessor   its callback runs on the MAIN THREAD and is skipped when the page is busy
//                         (documented in gmeet-capture/src/pcm-capture.ts, the module written to
//                          replace it — the bot's mixed lane is the last path still on it)
//
// Nothing recorded can separate them: a buffer the processor never delivered was never peak-tested
// either. So drive the REAL capture module in a REAL browser over a SYNTHETIC source whose silence
// structure and main-thread load are ours to set, and read the counters it now keeps against the
// AudioContext's own clock.
//
//   node src/capture-bench.mjs [--sec 20]
//
// Arms: continuous tone (no silence to gate) · speech-like with digital-silence pauses (what WebRTC
// silence suppression produces) · continuous tone under main-thread load. The first isolates the
// processor, the second the gate, the third says whether load alone can produce the observed loss.
import { readFileSync } from 'node:fs';
import path from 'node:path';

const HERE = path.dirname(new URL(import.meta.url).pathname);
// eval is deliberately NOT a workspace member, so its deps are resolved by path — the same way it
// imports the capture bricks. Playwright belongs to @vexa/join, the only package that drives a browser.
const { chromium } = await import(new URL('../../modules/join/node_modules/playwright/index.mjs', import.meta.url).href);
const BUNDLE = path.join(HERE, '..', '..', 'services', 'bot', 'dist', 'browser-utils.global.js');

const SEC = Number(process.argv[process.argv.indexOf('--sec') + 1] || 20);

/** Runs inside the page: build a live MediaStream, capture it, report the counters. */
async function runArm(page, { silence, load, sec }) {
  return page.evaluate(async ({ silence, load, sec }) => {
    const SR = 16000;
    const ctx = new AudioContext({ sampleRate: SR });
    await ctx.resume();
    const osc = ctx.createOscillator();
    osc.frequency.value = 220;
    const gain = ctx.createGain();
    const dest = ctx.createMediaStreamDestination();
    osc.connect(gain).connect(dest);

    // Speech-like: 1.2s of tone, 0.6s of DIGITAL silence (gain exactly 0, which is what a codec's
    // silence suppression yields — not a noise floor). Continuous: gain pinned at speech level.
    const t0 = ctx.currentTime + 0.05;
    if (silence) {
      for (let t = 0, i = 0; t < sec + 2; t += 1.8, i++) {
        gain.gain.setValueAtTime(0.3, t0 + t);
        gain.gain.setValueAtTime(0.0, t0 + t + 1.2);
      }
    } else {
      gain.gain.setValueAtTime(0.3, t0);
    }
    osc.start();

    let frames = 0;
    const cap = await window.VexaBrowserUtils.createMixedAudioCapture(
      dest.stream, () => { frames++; }, { sampleRate: SR, replay: false, log: () => { /* quiet */ } },
    );

    // Main-thread load: block the event loop in bursts, which is what a live meeting client plus a
    // software-rendered page does to a ScriptProcessor callback.
    let loader = null;
    if (load) loader = setInterval(() => { const end = Date.now() + 180; while (Date.now() < end) { /* block */ } }, 220);

    await new Promise((r) => setTimeout(r, sec * 1000));
    if (loader) clearInterval(loader);
    const s = cap.stats();
    cap.stop();
    try { osc.stop(); ctx.close(); } catch { /* */ }
    return { ...s, framesDelivered: frames };
  }, { silence, load, sec });
}

const browser = await chromium.launch({
  args: ['--autoplay-policy=no-user-gesture-required', '--mute-audio'],
});
const page = await browser.newPage();
await page.setContent('<!doctype html><title>capture-bench</title><body></body>');
await page.addScriptTag({ content: readFileSync(BUNDLE, 'utf8') });

const ARMS = [
  { name: 'continuous tone, idle page', silence: false, load: false },
  { name: 'speech-like + digital silence, idle page', silence: true, load: false },
  { name: 'continuous tone, main thread BUSY', silence: false, load: true },
  { name: 'speech-like + silence, main thread BUSY', silence: true, load: true },
];

console.log(`capture-bench — ${SEC}s per arm, real createMixedAudioCapture in real chromium\n`);
console.log('arm'.padEnd(42) + 'duty   gated  processor-deficit');
for (const arm of ARMS) {
  const s = await runArm(page, { ...arm, sec: SEC });
  const duty = s.renderedSec > 0 ? s.deliveredSec / s.renderedSec : 0;
  console.log(
    arm.name.padEnd(42) +
    `${(duty * 100).toFixed(1)}%`.padEnd(7) +
    `${s.gatedSec.toFixed(1)}s`.padEnd(7) +
    `${s.processorDeficitSec.toFixed(1)}s   ` +
    `(seen ${s.seen}, emitted ${s.emitted}, rendered ${s.renderedSec.toFixed(1)}s)`);
}
await browser.close();

console.log(`
Reading it: 'duty' is what the SESSION RECORDING would show — buffers that reached the callback over
the audio the graph rendered. 'gated' and 'processor-deficit' split that loss into the two
mechanisms, which no recording can do on its own.`);
