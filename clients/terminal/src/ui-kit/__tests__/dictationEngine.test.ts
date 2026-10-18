import { describe, expect, it } from "vitest";
import { createDictationEngine } from "../dictationEngine";
import { micErrorMessage } from "../micDictation";
import { encodeFlac } from "../flac";

const SR = 16000;
const FRAME = 1024;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A fake STT: the "words" are the distinct frame amplitudes in the window, answered after `ms`. */
function fakeStt(ms: number, calls: { final: boolean; sec: number }[] = []) {
  return async (pcm: Float32Array, _prompt: string, final: boolean) => {
    calls.push({ final, sec: pcm.length / SR });
    await sleep(ms);
    const words: string[] = [];
    for (let i = 0; i < pcm.length; i += FRAME) {
      const v = Math.round(pcm[i] * 1000);
      if (v >= 10 && words[words.length - 1] !== `w${v}`) words.push(`w${v}`);
    }
    return { text: words.join(" ") };
  };
}

async function speak(engine: ReturnType<typeof createDictationEngine>, amp: number, sec: number) {
  for (let t = 0; t < sec * SR; t += FRAME) { engine.feed(new Float32Array(FRAME).fill(amp)); engine.tick(); await sleep((FRAME / SR) * 1000 / 8); }
}

describe("dictation engine", () => {
  it("shows a draft while still speaking, finalizes at the pause, and sends nothing while quiet", async () => {
    const calls: { final: boolean; sec: number }[] = [];
    const updates: [string, string, number][] = [];
    const t0 = Date.now();
    let fake = 0;
    const e = createDictationEngine({ transcribe: fakeStt(20, calls), onUpdate: (f, d) => updates.push([f, d, Date.now() - t0]),
      submitMs: 50, minDraftSec: 0.25, pauseMs: 80, now: () => Date.now() + fake });
    await speak(e, 0.02, 1);                   // "w20", 1 s of speech (fed 8× faster than real time)
    expect(updates.some(([, d]) => d === "w20")).toBe(true);
    await speak(e, 0, 0.3);                     // a pause
    await sleep(100); e.tick(); await sleep(60);
    expect(e.finalText).toBe("w20");
    const quietCalls = calls.length;
    await speak(e, 0, 1);                       // silence is never sent
    expect(calls.length).toBe(quietCalls);
    expect(calls.some((c) => c.final)).toBe(true);
  });

  it("keeps finals in spoken order even when a later one answers first, and stop returns all of it", async () => {
    const order: string[] = [];
    let n = 0;
    const e = createDictationEngine({
      transcribe: async (pcm, _p, final) => {
        const mine = ++n;
        if (final) await sleep(mine === 1 ? 150 : 10);   // the first final is the slow one
        return { text: Math.round(pcm[0] * 1000) >= 30 ? "second" : "first" };
      },
      onUpdate: (f) => order.push(f), submitMs: 1000, minDraftSec: 5, pauseMs: 40, maxWindowSec: 10,
    });
    await speak(e, 0.02, 0.3); await speak(e, 0, 0.1); await sleep(60); e.tick();
    await speak(e, 0.03, 0.3);
    const text = await e.stop();
    expect(text).toBe("first second");
    expect(order.filter(Boolean)[0]).toBe("first");
  });

  it("never sends a window longer than the cap", async () => {
    const calls: { final: boolean; sec: number }[] = [];
    const e = createDictationEngine({ transcribe: fakeStt(5, calls), onUpdate: () => {}, submitMs: 20, maxWindowSec: 2, pauseMs: 10_000 });
    await speak(e, 0.05, 5);
    await e.stop();
    expect(Math.max(...calls.map((c) => c.sec))).toBeLessThanOrEqual(2.1);
  });
});

describe("mic errors in plain words", () => {
  it("names what to do", () => {
    expect(micErrorMessage({ name: "NotAllowedError" })).toMatch(/blocked.*Allow the microphone/);
    expect(micErrorMessage({ name: "NotFoundError" })).toMatch(/No microphone/);
    expect(micErrorMessage({ name: "NotReadableError" })).toMatch(/busy in another app/);
  });
});

describe("flac upload", () => {
  it("is a FLAC stream about half the WAV size for speech-like audio", () => {
    const x = new Float32Array(SR * 2);
    for (let i = 0; i < x.length; i++) x[i] = 0.2 * Math.sin(i / 9) * Math.abs(Math.sin(i / 2000));
    const f = encodeFlac(x, SR);
    expect(String.fromCharCode(...f.subarray(0, 4))).toBe("fLaC");
    expect(f.length).toBeLessThan(x.length * 2 * 0.7);
  });
});

describe("dictation on pauses and room noise", () => {
  it("never shows a phrase Whisper invents on silence, in any language it knows", async () => {
    const updates: string[] = [];
    const e = createDictationEngine({
      transcribe: async () => ({ text: "привет Продолжение следует... Продолжение следует... Субтитры сделал DimaTorzok" }),
      onUpdate: (f, d) => updates.push(`${f}|${d}`), submitMs: 20, minDraftSec: 0.25, pauseMs: 60,
    });
    await speak(e, 0.05, 0.6); await speak(e, 0, 0.3); await sleep(80); e.tick();
    const text = await e.stop();
    expect(text).toBe("привет");
    expect(updates.join(" ")).not.toMatch(/Продолжение|Субтитры/);
  });
  it("sends nothing for the low noise left after the browser's noise suppression", async () => {
    let calls = 0;
    const e = createDictationEngine({ transcribe: async () => { calls++; return { text: "Hvað er hljóði?" }; }, onUpdate: () => {}, submitMs: 20, minDraftSec: 0.25 });
    let s = 3;
    for (let t = 0; t < 4 * SR; t += FRAME) {
      const f = new Float32Array(FRAME);
      for (let k = 0; k < FRAME; k++) { s = (s * 1103515245 + 12345) % 2147483648; f[k] = ((s / 2147483648) * 2 - 1) * 0.012; }   // RMS ≈ 0.007
      e.feed(f); e.tick(); await sleep(2);
    }
    await e.stop();
    expect(calls).toBe(0);
  });
});
