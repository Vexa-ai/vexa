import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createStore } from "../../platform/core";
import { AlloySttTelemetryMonitor } from "../../workbench/AlloySttTelemetryMonitor";
import {
  createAlloySttTelemetryPoller,
  type AlloySttTelemetryPoller,
  type AlloySttTelemetryState,
} from "../../workbench/alloySttTelemetry";

vi.mock("../App", () => ({
  App: () => null,
}));

import Page from "../page";

const meeting = {
  version: 1 as const,
  meeting_id: "41",
  native_meeting_id: "raw-meeting",
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
};

let host: HTMLDivElement | null = null;
let root: Root | null = null;
const reactTestEnvironment = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT: boolean;
};

function mount(element: ReactElement) {
  reactTestEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(element));
  return host;
}

afterEach(() => {
  if (root) {
    act(() => root!.unmount());
  }
  host?.remove();
  host = null;
  root = null;
  delete process.env.ALLOY_STT_TELEMETRY;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ALLOY telemetry server runtime flag", () => {
  it("evaluates unset -> false, trimmed 1 -> true, and 0 -> false without reimporting Page", () => {
    const currentFlag = () =>
      (Page() as ReactElement<{
        alloySttTelemetryEnabled: boolean;
      }>).props.alloySttTelemetryEnabled;

    delete process.env.ALLOY_STT_TELEMETRY;
    expect(currentFlag()).toBe(false);

    process.env.ALLOY_STT_TELEMETRY = " 1 ";
    expect(currentFlag()).toBe(true);

    process.env.ALLOY_STT_TELEMETRY = "0";
    expect(currentFlag()).toBe(false);
  });

  it("renders the upstream footer with zero subscription, start, timer, or fetch when disabled", async () => {
    reactTestEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    const fetchStatus = vi.fn(async () => {
      throw new Error("disabled telemetry must not fetch");
    });
    const poller = createAlloySttTelemetryPoller({
      documentRef: null,
      fetchStatus,
    });
    const subscribe = vi.spyOn(poller.store, "subscribe");
    const start = vi.spyOn(poller, "start");
    const interval = vi.spyOn(globalThis, "setInterval");

    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root!.render(
        <AlloySttTelemetryMonitor
          enabled={false}
          poller={poller}
          disabledFallback={<button type="button">reset layout</button>}
        />,
      );
    });

    expect(host.textContent).toBe("reset layout");
    expect(subscribe).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
    expect(interval).not.toHaveBeenCalled();
    expect(fetchStatus).not.toHaveBeenCalled();
  });
});

describe("ALLOY telemetry server-owned aggregate", () => {
  it("uses conflicting aggregate values and health for the footer while retaining raw meeting details", () => {
    const state = {
      enabled: true,
      available: true,
      fetchedAtMs: 12_000,
      transportError: null,
      aggregate: {
        meetings: 7,
        active_requests: 4,
        waiting_channels: 5,
        queued_audio_sec: 12.5,
        lag_sec: 8.5,
        rtf: 1.75,
        health: "red" as const,
      },
      meetings: [meeting],
    } as AlloySttTelemetryState;
    const poller: AlloySttTelemetryPoller = {
      store: createStore(state),
      start: vi.fn(),
      stop: vi.fn(),
      pollNow: vi.fn(async () => undefined),
    };

    const rendered = mount(
      <AlloySttTelemetryMonitor enabled poller={poller} now={() => 12_000} />,
    );

    expect(rendered.textContent).toContain("STT 7");
    expect(rendered.textContent).toContain("4 active");
    expect(rendered.textContent).toContain("5 waiting");
    expect(rendered.textContent).toContain("12.5s queued");
    expect(rendered.textContent).toContain("lag 8.5s");
    expect(rendered.textContent).toContain("RTF 1.75");
    expect(rendered.textContent).toContain("health red");

    const button = rendered.querySelector<HTMLButtonElement>(
      'button[aria-label="Open STT telemetry details"]',
    );
    expect(button?.style.color).toBe("rgb(209, 77, 87)");
    act(() => button!.click());
    expect(rendered.textContent).toContain("raw-meeting");
  });
});
