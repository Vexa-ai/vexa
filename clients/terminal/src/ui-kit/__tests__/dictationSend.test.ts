import { describe, expect, it } from "vitest";
import { createDictationEngine } from "../dictationEngine";
import { createComposerDictation } from "../composerDictation";

const SR = 16000;
const FRAME = 1024;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Fake STT: one word per distinct frame amplitude in the window ("w20", "w30" …), answered after `ms`. */
function fakeStt(ms: number) {
  return async (pcm: Float32Array) => {
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
const opts = { submitMs: 30, minDraftSec: 0.25, pauseMs: 60 };

/** The composer half: a text box value, the controller, and an engine wired as chat.tsx wires them. */
function rig(sttMs: number) {
  let box = "";
  const c = createComposerDictation({ setValue: (v) => { box = v; } });
  c.start(box);
  const e = createDictationEngine({ transcribe: fakeStt(sttMs), onUpdate: c.update, ...opts });
  c.attach(e);
  return { c, e, box: () => box, type: (v: string) => { box = v; c.edited(v); } };
}

describe("send during dictation", () => {
  it("empties the composer and keeps it empty when the pre-send final answers late", async () => {
    const r = rig(150);                                  // slow STT: the final lands after the send
    await speak(r.e, 0.02, 0.6);
    await sleep(200);                                    // a draft "w20" is in the box
    expect(r.box()).toBe("w20");
    await speak(r.e, 0, 0.2); r.e.tick();                // pause → final for the sent audio is in flight
    r.type(""); r.c.sent();                              // Enter: the message leaves, the box is cleared
    expect(r.box()).toBe("");
    await sleep(300);                                    // the late final for pre-send audio arrives
    expect(r.box()).toBe("");
    expect(r.e.finalText).toBe("");
  });

  it("shows only speech after the send, and stop after send leaves only that", async () => {
    const r = rig(10);
    await speak(r.e, 0.02, 0.5); await speak(r.e, 0, 0.2); r.e.tick(); await sleep(80);
    expect(r.box()).toBe("w20");
    r.type(""); r.c.sent();
    await speak(r.e, 0.03, 0.5); await speak(r.e, 0, 0.2); r.e.tick(); await sleep(80);
    expect(r.box()).toBe("w30");
    r.c.finish(await r.e.stop());
    expect(r.box()).toBe("w30");
  });

  it("stop right after send leaves an empty composer", async () => {
    const r = rig(100);
    await speak(r.e, 0.02, 0.5);
    r.type(""); r.c.sent();
    r.c.finish(await r.e.stop());
    expect(r.box()).toBe("");
  });

  it("keeps a person's edit; later speech is appended after it, not over it", async () => {
    const r = rig(10);
    await speak(r.e, 0.02, 0.5); await speak(r.e, 0, 0.2); r.e.tick(); await sleep(80);
    expect(r.box()).toBe("w20");
    r.type("What's the company?");                       // the person corrects the dictated text
    await sleep(50);
    expect(r.box()).toBe("What's the company?");
    await speak(r.e, 0.03, 0.5); await speak(r.e, 0, 0.2); r.e.tick(); await sleep(80);
    expect(r.box()).toBe("What's the company? w30");
  });
});
