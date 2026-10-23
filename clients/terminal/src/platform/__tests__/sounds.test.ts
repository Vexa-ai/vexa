/** Notification sounds (0.13.4 S6): the pure decision matrix, and a module that stays silent and
 *  safe where there is no AudioContext (jsdom, old browsers). */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  __resetSounds, audioSupported, inQuietHours, play, preview, ringing, setVolume, soundFor, startRing, stopRing, unlock, unlocked,
  type SoundContext,
} from "../sounds";

const ctx = (over: Partial<SoundContext> = {}): SoundContext => ({
  sounds: { on: true, chime: true, tick: true, ring: true, volume: 0.6 },
  quiet: { on: true, from: "22:00", to: "08:00" },
  roomInView: null,
  nowMinutes: 14 * 60,
  ...over,
});
const n = (kind: string, room = "s-1") => ({ kind, object: { kind: "chat", id: room } });

afterEach(() => { __resetSounds(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("soundFor", () => {
  it("a plain message never sounds", () => {
    for (const k of ["chat_message", "routine_posted", "meeting_shared", "chat_added", "rules_decided", ""]) expect(soundFor(n(k), ctx())).toBeNull();
  });
  it("a call rings; a mention, a waiting or slipped card and an invite chime", () => {
    expect(soundFor(n("call_started"), ctx())).toBe("ring");
    for (const k of ["chat_mentioned", "card_waiting", "card_slipped", "workspace_invited"]) expect(soundFor(n(k), ctx())).toBe("chime");
  });
  it("never for the room the person is looking at", () => {
    expect(soundFor(n("chat_mentioned", "s-1"), ctx({ roomInView: "s-1" }))).toBeNull();
    expect(soundFor(n("call_started", "s-1"), ctx({ roomInView: "s-1" }))).toBeNull();
    expect(soundFor(n("chat_mentioned", "s-2"), ctx({ roomInView: "s-1" }))).toBe("chime");
  });
  it("never in quiet hours, including across midnight", () => {
    expect(soundFor(n("call_started"), ctx({ nowMinutes: 23 * 60 }))).toBeNull();
    expect(soundFor(n("chat_mentioned"), ctx({ nowMinutes: 7 * 60 + 59 }))).toBeNull();
    expect(soundFor(n("chat_mentioned"), ctx({ nowMinutes: 8 * 60 }))).toBe("chime");
    expect(soundFor(n("chat_mentioned"), ctx({ nowMinutes: 23 * 60, quiet: { on: false, from: "22:00", to: "08:00" } }))).toBe("chime");
    expect(inQuietHours({ on: true, from: "09:00", to: "17:00" }, 12 * 60)).toBe(true);
    expect(inQuietHours({ on: true, from: "09:00", to: "17:00" }, 18 * 60)).toBe(false);
    expect(inQuietHours({ on: true, from: "bad", to: "17:00" }, 12 * 60)).toBe(false);
  });
  it("never when sounds are off, or that one sound is off", () => {
    expect(soundFor(n("call_started"), ctx({ sounds: { on: false, chime: true, tick: true, ring: true, volume: 1 } }))).toBeNull();
    expect(soundFor(n("call_started"), ctx({ sounds: { on: true, chime: true, tick: true, ring: false, volume: 1 } }))).toBeNull();
    expect(soundFor(n("chat_mentioned"), ctx({ sounds: { on: true, chime: false, tick: true, ring: true, volume: 1 } }))).toBeNull();
  });
});

describe("without an AudioContext", () => {
  it("every call is a silent no-op", () => {
    vi.stubGlobal("AudioContext", undefined);
    expect(audioSupported()).toBe(false);
    expect(() => { unlock(); play("chime"); play("ring"); setVolume(2); preview("ring"); startRing(); stopRing(); }).not.toThrow();
    expect(unlocked()).toBe(false);
    expect(ringing()).toBe(false);
  });
});

describe("with a (fake) AudioContext", () => {
  function fakeAudio() {
    const oscillators: unknown[] = [];
    const param = () => ({ value: 0, setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() });
    class Fake {
      state = "suspended"; currentTime = 0; destination = {};
      resume() { this.state = "running"; return Promise.resolve(); }
      createGain() { return { gain: param(), connect: vi.fn() }; }
      createOscillator() { const o = { type: "", frequency: param(), connect: vi.fn(), start: vi.fn(), stop: vi.fn() }; oscillators.push(o); return o; }
    }
    vi.stubGlobal("AudioContext", Fake);
    return oscillators;
  }
  it("plays only after unlock; the ring repeats every 1.6 s and ends by itself at 30 s", async () => {
    const osc = fakeAudio();
    play("chime");
    expect(osc).toHaveLength(0);
    unlock();
    await Promise.resolve();
    expect(unlocked()).toBe(true);
    play("chime");
    expect(osc).toHaveLength(2);
    vi.useFakeTimers();
    const end = vi.fn();
    startRing(end);
    const after1 = osc.length;
    vi.advanceTimersByTime(1600);
    expect(osc.length).toBeGreaterThan(after1);
    expect(ringing()).toBe(true);
    vi.advanceTimersByTime(30000);
    expect(ringing()).toBe(false);
    expect(end).toHaveBeenCalledOnce();
  });
});
