"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createHttpApiClient, type ApiClient } from "@vexa/dash-api-client";
import { createWsClient } from "@vexa/dash-ws";
import type { WsClientFactory } from "@vexa/dash-meeting-state";
import { createBrowserWsTransport } from "@/lib/browser-ws-transport";

/** The runtime config the browser reads from `/api/config`. */
export interface BrowserRuntimeConfig {
  apiUrl: string;
  wsUrl: string;
  authToken: string | null;
  defaultBotName: string | null;
}

interface VexaContextValue {
  /** REST port — always routed through the same-origin `/api/vexa` proxy (auth injected server-side). */
  apiClient: ApiClient;
  /** Builds a live WS client wired to a meeting-state store's reducers. Ready once config has loaded. */
  wsClientFactory: WsClientFactory;
  config: BrowserRuntimeConfig | null;
  /** True once `/api/config` has resolved — gate live features on this. */
  ready: boolean;
}

const VexaContext = createContext<VexaContextValue | null>(null);

/** Same-origin `/ws`, with the dashboard origin's proto upgraded (ws/wss). The Next rewrite proxies it. */
function sameOriginWsUrl(): string {
  if (typeof window === "undefined") return "/ws";
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws`;
}

export function Providers({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<BrowserRuntimeConfig | null>(null);
  const configRef = useRef<BrowserRuntimeConfig | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/config")
      .then(async (r) => {
        // DF3 — a non-ok /api/config (e.g. a 500 when VEXA_API_URL is unset) must NOT be parsed as
        // config: its body is an error envelope, not BrowserRuntimeConfig. Leaving config null is the
        // honest "misconfigured" state — never a silently-wrong config with undefined wsUrl/authToken.
        if (!r.ok) {
          console.error(`[providers] /api/config returned ${r.status} — dashboard misconfigured`);
          return null;
        }
        return (await r.json()) as BrowserRuntimeConfig;
      })
      .then((c) => {
        if (!alive || !c) return;
        configRef.current = c;
        setConfig(c);
      })
      .catch((e) => console.error("[providers] /api/config failed:", e));
    return () => {
      alive = false;
    };
  }, []);

  // The REST client never needs the browser token — the `/api/vexa` proxy injects it server-side.
  const apiClient = useMemo(() => createHttpApiClient({ baseUrl: "/api/vexa" }), []);

  // The WS factory reads the latest config from the ref at connect time, so it's stable across renders
  // yet always uses the resolved wsUrl/authToken (config arrives after mount).
  const wsClientFactory = useMemo<WsClientFactory>(() => {
    return (wiring) => {
      const cfg = configRef.current;
      const wsUrl = cfg?.wsUrl || sameOriginWsUrl();
      const authToken = cfg?.authToken || "";
      return createWsClient({
        transport: createBrowserWsTransport(),
        wsUrl,
        authToken,
        meeting: wiring.meeting,
        onStatus: wiring.onStatus,
        onTranscript: wiring.onTranscript,
        onChat: wiring.onChat,
        onError: wiring.onError,
      });
    };
  }, []);

  const value = useMemo<VexaContextValue>(
    () => ({ apiClient, wsClientFactory, config, ready: config !== null }),
    [apiClient, wsClientFactory, config]
  );

  return <VexaContext.Provider value={value}>{children}</VexaContext.Provider>;
}

export function useVexa(): VexaContextValue {
  const ctx = useContext(VexaContext);
  if (!ctx) throw new Error("useVexa must be used within <Providers>");
  return ctx;
}
