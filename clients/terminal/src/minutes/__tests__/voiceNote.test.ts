/** Voice notes (#1783): the browser half — capture.v1 framing, resampling to 16 kHz, and the socket
 *  protocol (ready → frames → stop → stopped), driven with an injected microphone and socket. */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FRAME_SAMPLES, SAMPLE_RATE, createFramer, createResampler, encodeAudioFrame, resetVoiceNote,
  endVoiceNote, startVoiceNote, stopVoiceNote, voiceNoteState, type VoiceNoteDeps,
} from "../voiceNote";

afterEach(() => { resetVoiceNote(); vi.useRealTimers(); });

describe("capture.v1 frame", () => {
  it("is the no-name audio frame: Int32 track, Float64 capture ms, Float32 PCM, little-endian", () => {
    const buf = encodeAudioFrame(0, 1_760_000_000_123.5, Float32Array.of(0.5, -0.25));
    const v = new DataView(buf);
    expect(buf.byteLength).toBe(12 + 8);
    expect(v.getInt32(0, true)).toBe(0);
    expect(v.getFloat64(4, true)).toBe(1_760_000_000_123.5);
    expect([v.getFloat32(12, true), v.getFloat32(16, true)]).toEqual([0.5, -0.25]);
  });
});

describe("resampler", () => {
  it("turns 48 kHz into 16 kHz with no drift across chunks", () => {
    const r = createResampler(48_000);
    let n = 0;
    for (let k = 0; k < 100; k++) n += r(new Float32Array(480).fill(0.1)).length;   // 1 s in 10 ms chunks
    expect(Math.abs(n - SAMPLE_RATE)).toBeLessThanOrEqual(1);
  });
  it("keeps a ramp a ramp across a chunk join", () => {
    const r = createResampler(32_000);
    const ramp = Float32Array.from({ length: 64 }, (_, i) => i);
    const out = [...r(ramp.slice(0, 31)), ...r(ramp.slice(31))];
    out.forEach((x, i) => expect(x).toBeCloseTo(i * 2, 5));
  });
});

describe("framer", () => {
  it("cuts 100 ms frames stamped from the capture start, and flushes the tail", () => {
    const frames: [number, number][] = [];
    const f = createFramer(1_000_000, (pcm, ts) => frames.push([pcm.length, ts]));
    f.push(new Float32Array(FRAME_SAMPLES * 2 + 100));
    expect(frames).toEqual([[FRAME_SAMPLES, 1_000_000], [FRAME_SAMPLES, 1_000_100]]);
    f.flush();
    expect(frames[2]).toEqual([100, 1_000_200]);
  });
});

class FakeSocket {
  binaryType = "";
  readyState = 0;
  sent: (ArrayBuffer | string)[] = [];
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  send(d: ArrayBuffer | string) {
    this.sent.push(d);
    if (typeof d === "string" && JSON.parse(d).type === "stop") {
      queueMicrotask(() => this.serve({ type: "stopped", received_sec: 0.2 }));
    }
  }
  close() { this.readyState = 3; }
  serve(msg: object) { this.onmessage?.({ data: JSON.stringify(msg) }); }
  open() { this.readyState = 1; this.serve({ type: "ready", max_duration_sec: 1800, received_sec: 0 }); }
}

function deps(socket: FakeSocket, calls: { url: string; init?: RequestInit }[]) {
  let feed: ((pcm: Float32Array) => void) | null = null;
  const d: VoiceNoteDeps = {
    fetch: (async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ id: 42, native_meeting_id: "vn-abc" }), { status: 201 });
    }) as typeof fetch,
    openSocket: (path) => { calls.push({ url: `ws:${path}` }); return socket as unknown as WebSocket; },
    openMicrophone: async (onChunk) => { feed = onChunk; mic.open = true; return { sampleRate: 16_000, stop: () => { feed = null; mic.open = false; } }; },
    now: () => 1_760_000_000_000,
  };
  const mic = { open: false };
  return { d, mic, speak: (n: number) => feed?.(new Float32Array(n).fill(0.05)) };
}

describe("recorder", () => {
  it("makes a voice_note meeting, streams frames after ready, and Stop sends the rest then the end", async () => {
    const socket = new FakeSocket();
    const calls: { url: string; init?: RequestInit }[] = [];
    const { d, speak } = deps(socket, calls);
    const m = await startVoiceNote(d);
    expect(m).toEqual({ id: "42", native_meeting_id: "vn-abc" });
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ platform: "voice_note" });
    expect(calls[1].url).toBe("ws:/voice-notes/vn-abc/stream");
    speak(FRAME_SAMPLES);                       // before ready: queued
    expect(socket.sent).toHaveLength(0);
    socket.open();
    expect(voiceNoteState().phase).toBe("recording");
    expect(socket.sent).toHaveLength(1);        // the queue drained on ready
    speak(FRAME_SAMPLES + 50);
    await stopVoiceNote();
    const frames = socket.sent.filter((x) => typeof x !== "string") as ArrayBuffer[];
    expect(frames.map((f) => (f.byteLength - 12) / 4)).toEqual([FRAME_SAMPLES, FRAME_SAMPLES, 50]);
    expect(new DataView(frames[1]).getFloat64(4, true)).toBe(1_760_000_000_100);
    expect(socket.sent.at(-1)).toBe(JSON.stringify({ type: "stop" }));
    expect(voiceNoteState()).toMatchObject({ phase: "stopped", receivedSec: 0.2 });
  });

  it("a typed refusal from the socket becomes words a person can act on", async () => {
    const socket = new FakeSocket();
    const { d } = deps(socket, []);
    await startVoiceNote(d);
    socket.serve({ type: "error", error: "insufficient_scope" });
    expect(voiceNoteState().phase).toBe("error");
    expect(voiceNoteState().error).toMatch(/bot scope/);
  });

  it("a refused microphone makes no meeting", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const { d } = deps(new FakeSocket(), calls);
    d.openMicrophone = async () => { throw new DOMException("denied", "NotAllowedError"); };
    await expect(startVoiceNote(d)).rejects.toThrow(/Microphone access was refused/);
    expect(calls).toHaveLength(0);
    expect(voiceNoteState().phase).toBe("error");
  });

  // A PHONE (2026-10-13): its socket was refused with the sign-in key and the meeting view said only
  // "live · 0 lines" while the microphone stayed on. Each silent way a note can fail now says so.
  it("a refused sign-in on the socket says what to do and releases the microphone", async () => {
    const socket = new FakeSocket();
    const { d, mic } = deps(socket, []);
    await startVoiceNote(d);
    socket.serve({ type: "error", error: "invalid_api_key" });
    expect(voiceNoteState().phase).toBe("error");
    expect(voiceNoteState().error).toMatch(/sign in again/i);
    expect(mic.open).toBe(false);
  });

  it("a socket that never says ready fails in seconds, not silently", async () => {
    vi.useFakeTimers();
    const socket = new FakeSocket();
    const { d, mic } = deps(socket, []);
    await startVoiceNote(d);
    expect(voiceNoteState().phase).toBe("starting");
    await vi.advanceTimersByTimeAsync(15_000);
    expect(voiceNoteState().phase).toBe("error");
    expect(voiceNoteState().error).toMatch(/could not reach the recorder/i);
    expect(mic.open).toBe(false);
    expect(socket.readyState).toBe(3);
  });

  it("a microphone that delivers nothing is named while recording, and clears when audio comes", async () => {
    vi.useFakeTimers();
    const socket = new FakeSocket();
    const { d, speak } = deps(socket, []);
    await startVoiceNote(d);
    socket.open();
    await vi.advanceTimersByTimeAsync(6_000);
    expect(voiceNoteState().warning).toMatch(/no audio from your microphone/i);
    speak(FRAME_SAMPLES);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(voiceNoteState().warning).toBeUndefined();
  });

  it("a note the server ended releases the microphone and leaves the recording state", async () => {
    const socket = new FakeSocket();
    const { d, mic } = deps(socket, []);
    await startVoiceNote(d);
    socket.open();
    endVoiceNote("vn-other");
    expect(mic.open).toBe(true);
    endVoiceNote("vn-abc");
    expect(mic.open).toBe(false);
    expect(voiceNoteState().phase).toBe("stopped");
    expect(socket.readyState).toBe(3);
  });
});
