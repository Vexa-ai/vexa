import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createStore } from "../../platform/core";
import { AlloySttTelemetryMonitor } from "../AlloySttTelemetryMonitor";
import type {
  AlloySttTelemetryPoller,
  AlloySttTelemetryState,
} from "../alloySttTelemetry";

const state: AlloySttTelemetryState = {
  enabled: true,
  available: true,
  fetchedAtMs: 12_000,
  transportError: null,
  meetings: [{
    version: 1,
    meeting_id: "41",
    native_meeting_id: "abc-defg-hij",
    updated_at_ms: 10_000,
    active_requests: 1,
    active_audio_sec: 2,
    waiting_channels: 1,
    queued_audio_sec: 3,
    latest_captured_audio_end_ms: 10_000,
    latest_processed_audio_end_ms: 7_000,
    lag_sec: 3,
    rtf_ema: 0.8,
    processed_windows: 4,
    superseded_windows: 2,
    last_error: null,
  }],
};

describe("ALLOY STT telemetry monitor", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.restoreAllMocks();
  });

  it("shows live queue totals globally and expands per-meeting details", () => {
    const store = createStore(state);
    const poller: AlloySttTelemetryPoller = {
      store,
      start: vi.fn(),
      stop: vi.fn(),
      pollNow: vi.fn(async () => undefined),
    };

    act(() => root.render(
      <AlloySttTelemetryMonitor poller={poller} now={() => 12_000} />,
    ));

    expect(host.textContent).toContain("STT 1");
    expect(host.textContent).toContain("1 active");
    expect(host.textContent).toContain("1 waiting");
    expect(host.textContent).toContain("3.0s queued");
    expect(host.textContent).toContain("lag 3.0s");
    expect(host.textContent).toContain("RTF 0.80");
    expect(poller.start).toHaveBeenCalledOnce();

    const button = host.querySelector<HTMLButtonElement>(
      'button[aria-label="Open STT telemetry details"]',
    );
    expect(button).not.toBeNull();
    act(() => button!.click());

    expect(host.textContent).toContain("abc-defg-hij");
    expect(host.textContent).toContain("2 superseded");
  });

  it("restores the upstream footer action when telemetry is disabled", () => {
    const store = createStore({
      ...state,
      enabled: false,
      available: false,
      meetings: [],
    });
    const poller: AlloySttTelemetryPoller = {
      store,
      start: vi.fn(),
      stop: vi.fn(),
      pollNow: vi.fn(async () => undefined),
    };

    act(() => root.render(
      <AlloySttTelemetryMonitor
        poller={poller}
        disabledFallback={<button type="button">reset layout</button>}
      />,
    ));

    expect(host.textContent).toBe("reset layout");
  });
});
