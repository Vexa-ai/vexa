/** Settings → Notifications (0.13.4 S6) and the sound wire on incoming notices.
 *    load        the person's policy fills every control; WhatsApp is a disabled "Coming soon" row
 *    save        each change PUTs only the key it changed
 *    rollback    a refusal puts the old value back and says one line
 *    clamp       the attention budget never offers more than the server's ceiling
 *    rooms       a level is saved as rooms["ws:<slug>" | "room:<id>"]
 *    sounds      a new interruption with audio locked raises "Enable sounds"; a call rings until answered */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

const toastMock = vi.fn();
vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: (...a: unknown[]) => toastMock(...a), Toaster: () => null }));

import { NotificationSettings, type Place } from "../NotificationSettings";
import { SettingsDialog } from "../SettingsDialog";
import { __resetPolicy, DEFAULT_POLICY, getPolicy, loadPolicy } from "../notificationPolicy";
import { __resetNoticeSounds, getNoticeSounds, startNoticeSounds } from "../noticeSounds";
import { __resetNotifications, handleFrame, receive } from "../notifications";
import { EnableSoundsPrompt } from "../EnableSounds";
import { __resetSounds, ringing } from "../../platform/sounds";

const PLACES: Place[] = [
  { key: "ws:growth", label: "Growth", kind: "workspace" },
  { key: "room:s-9", label: "#onboarding", kind: "room" },
];
const loadPlaces = () => Promise.resolve(PLACES);

function serve(opts: { put?: (body: Record<string, unknown>) => { status: number; body?: unknown }; ceiling?: number; policy?: Record<string, unknown> } = {}) {
  let policy = { ...DEFAULT_POLICY, rooms: { "ws:growth": "all" }, ...opts.policy } as Record<string, unknown>;
  const puts: Record<string, unknown>[] = [];
  const answer = () => ({ notifications: policy, timezone: "Europe/Lisbon", limits: { idle_floor_min: 5, budget_ceiling: opts.ceiling ?? 12 } });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/user/notifications" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      puts.push(body);
      const r = opts.put?.(body) ?? { status: 200 };
      if (r.status !== 200) return new Response(JSON.stringify({ detail: "bad" }), { status: r.status });
      policy = { ...policy, ...body };
      return new Response(JSON.stringify(answer()), { status: 200 });
    }
    if (url === "/api/user/notifications") return new Response(JSON.stringify(answer()), { status: 200 });
    if (url === "/api/auth/me") return new Response(JSON.stringify({ email: "pat@example.com" }), { status: 200 });
    return new Response("{}", { status: 404 });
  }));
  return puts;
}

beforeEach(() => { __resetPolicy(); toastMock.mockReset(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); __resetSounds(); __resetNoticeSounds(); __resetNotifications(); });

const sel = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;

describe("Settings → Notifications", () => {
  it("loads the policy into every control; WhatsApp is disabled and says Coming soon", async () => {
    serve();
    render(<NotificationSettings email="pat@example.com" loadPlaces={loadPlaces} />);
    await screen.findByText("Channels");
    expect(screen.getByText("pat@example.com")).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Email" }).getAttribute("aria-checked")).toBe("true");
    const wa = screen.getByRole("switch", { name: "WhatsApp" }) as HTMLButtonElement;
    expect(wa.disabled).toBe(true);
    expect(screen.getByText("Coming soon")).toBeTruthy();
    expect(sel("Nudge me outside Vexa").value).toBe("10");
    expect(sel("Digest").value).toBe("hourly");
    expect(sel("Quiet hours").value).toBe("22-08");
    expect(screen.getByText("Your time zone: Lisbon")).toBeTruthy();
    expect(sel("Attention budget").value).toBe("6");
    await screen.findByText("#onboarding");
    expect(sel("Notify for Growth").value).toBe("all");
    expect(screen.getByText(/Never in a notification: anyone.s personal data from a chat/)).toBeTruthy();
  });

  it("each change saves at once, only the key it changed", async () => {
    const puts = serve();
    render(<NotificationSettings email="pat@example.com" loadPlaces={loadPlaces} />);
    await screen.findByText("Channels");
    fireEvent.change(sel("Digest"), { target: { value: "daily" } });
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ digest: "daily" });
    expect(sel("Digest").value).toBe("daily");
    fireEvent.change(sel("Quiet hours"), { target: { value: "23-07" } });
    await waitFor(() => expect(puts).toHaveLength(2));
    expect(puts[1]).toEqual({ quiet: { on: true, from: "23:00", to: "07:00" } });
    await screen.findByText("#onboarding");
    fireEvent.change(sel("Notify for #onboarding"), { target: { value: "none" } });
    await waitFor(() => expect(puts).toHaveLength(3));
    expect(puts[2]).toEqual({ rooms: { "ws:growth": "all", "room:s-9": "none" } });
  });

  it("a refusal rolls the value back and says one line", async () => {
    serve({ put: () => ({ status: 422 }) });
    render(<NotificationSettings email="pat@example.com" loadPlaces={loadPlaces} />);
    await screen.findByText("Channels");
    fireEvent.click(screen.getByRole("switch", { name: "Email" }));
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith("That setting couldn't be saved just now"));
    expect(screen.getByRole("switch", { name: "Email" }).getAttribute("aria-checked")).toBe("true");
    expect(getPolicy().policy.channels.email).toBe(true);
  });

  it("the attention budget is clamped to the server's ceiling", async () => {
    serve({ ceiling: 8 });
    render(<NotificationSettings email="pat@example.com" loadPlaces={loadPlaces} />);
    await screen.findByText("Channels");
    const values = [...sel("Attention budget").options].map((o) => o.value);
    expect(values).toEqual(["4", "6", "8"]);
  });

  it("sits in the Settings dialog behind a Notifications tab", async () => {
    serve();
    render(<SettingsDialog onClose={() => undefined} initialTab="notifications" />);
    expect(screen.getByRole("tab", { name: "General" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Notifications" }).getAttribute("aria-selected")).toBe("true");
    await screen.findByText("Channels");
  });
});

describe("sounds on incoming notices", () => {
  const notice = (id: string, kind: string, extra: Record<string, unknown> = {}) =>
    ({ id, kind, text: "x", created: Date.now(), read: false, actor: { id: "u2" }, object: { kind: "chat", id: "s-1" }, ...extra });

  function fakeAudio() {
    const param = () => ({ value: 0, setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() });
    class Fake {
      state = "suspended"; currentTime = 0; destination = {};
      resume() { this.state = "running"; return Promise.resolve(); }
      createGain() { return { gain: param(), connect: vi.fn() }; }
      createOscillator() { return { type: "", frequency: param(), connect: vi.fn(), start: vi.fn(), stop: vi.fn() }; }
    }
    vi.stubGlobal("AudioContext", Fake);
  }

  it("an interruption with audio locked shows Enable sounds; a click unlocks", async () => {
    // quiet hours off so the wall clock does not matter (it failed at night: the default quiet hours)
    serve({ policy: { quiet: { on: false, from: "22:00", to: "08:00" } } });
    fakeAudio();
    __resetNotifications(fetch);
    const off = startNoticeSounds();
    await loadPolicy(true);
    render(<EnableSoundsPrompt />);
    act(() => receive(notice("n1", "chat_message")));
    expect(getNoticeSounds().needsEnable).toBe(false);
    act(() => receive(notice("n2", "chat_mentioned")));
    const btn = await screen.findByText("Enable sounds");
    fireEvent.click(btn);
    await waitFor(() => expect(screen.queryByText("Enable sounds")).toBeNull());
    off();
  });

  it("a call rings and stops when it is answered", async () => {
    // quiet hours off so the wall clock does not matter
    serve({ policy: { quiet: { on: false, from: "22:00", to: "08:00" } } });
    fakeAudio();
    __resetNotifications(fetch);
    const off = startNoticeSounds();
    await loadPolicy(true);
    const { enableSounds } = await import("../noticeSounds");
    enableSounds();
    await Promise.resolve();
    act(() => receive(notice("c1", "call_started")));
    expect(ringing()).toBe(true);
    act(() => handleFrame({ type: "notification.update", notification: notice("c1", "call_started", { read: true, state: "joined" }) as never, unread: 0 }));
    expect(ringing()).toBe(false);
    off();
  });
});
