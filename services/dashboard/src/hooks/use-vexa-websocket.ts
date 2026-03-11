"use client";

import { useEffect, useRef, useCallback } from "react";
import type {
  Platform,
  WebSocketIncomingMessage,
  TranscriptSegment,
  MeetingStatus,
} from "@/types/vexa";
import { useLiveStore } from "@/stores/live-store";
import { vexaAPI } from "@/lib/api";

interface UseVexaWebSocketOptions {
  platform: Platform;
  nativeId: string;
  onTranscript?: (segment: TranscriptSegment) => void;
  onStatusChange?: (status: MeetingStatus) => void;
  onError?: (error: string) => void;
  autoConnect?: boolean;
}

interface UseVexaWebSocketReturn {
  isConnecting: boolean;
  isConnected: boolean;
  error: string | null;
  connect: () => void;
  disconnect: () => void;
}

const PING_INTERVAL = 25000; // 25 seconds
const RECONNECT_DELAY = 3000; // 3 seconds

// Cache the config to avoid repeated API calls
let cachedConfig: { wsUrl: string; authToken: string | null } | null = null;

async function fetchConfig(): Promise<{ wsUrl: string; authToken: string | null }> {
  if (cachedConfig) return cachedConfig;

  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    cachedConfig = { wsUrl: config.wsUrl, authToken: config.authToken };
    return cachedConfig;
  } catch {
    // Fallback to default (runtime config should always be available)
    return {
      wsUrl: "ws://localhost:18056/ws",
      authToken: null
    };
  }
}

function buildWsUrl(baseUrl: string, authToken: string | null): string {
  if (!authToken) return baseUrl;
  const separator = baseUrl.includes("?") ? "&" : "?";
  return `${baseUrl}${separator}api_key=${encodeURIComponent(authToken)}`;
}

export function useVexaWebSocket(
  options: UseVexaWebSocketOptions
): UseVexaWebSocketReturn {
  const { platform, nativeId, onTranscript, onStatusChange, onError, autoConnect = true } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const shouldReconnectRef = useRef(true);

  const {
    isConnecting,
    isConnected,
    connectionError,
    setConnectionState,
    addLiveTranscript,
    bootstrapLiveTranscripts,
    setBotStatus,
  } = useLiveStore();

  const bootstrappedRef = useRef(false);

  const cleanup = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const message: WebSocketIncomingMessage = JSON.parse(event.data);

        switch (message.type) {
          case "transcript.mutable":
            // Process all segments from the payload (Vexa sends segments array)
            if (message.payload?.segments) {
              for (const seg of message.payload.segments) {
                // Skip empty segments or those missing required fields
                if (!seg.text?.trim() || !seg.absolute_start_time) continue;

                // Convert WebSocket segment to TranscriptSegment format
                const segment: TranscriptSegment = {
                  id: seg.absolute_start_time,
                  meeting_id: nativeId,
                  start_time: seg.start || 0,
                  end_time: seg.end_time || 0,
                  absolute_start_time: seg.absolute_start_time,
                  absolute_end_time: seg.absolute_end_time,
                  text: seg.text,
                  speaker: seg.speaker || "Unknown",
                  language: seg.language || "en",
                  completed: seg.completed,
                  session_uid: seg.session_uid || "",
                  created_at: seg.absolute_start_time,
                  updated_at: seg.updated_at,
                };
                addLiveTranscript(segment);
                onTranscript?.(segment);
              }
            }
            break;

          case "meeting.status":
            // Status is now in payload
            const status = message.payload?.status;
            if (status) {
              setBotStatus(status);
              onStatusChange?.(status);
            }
            break;

          case "subscribed":
            console.log("WebSocket: Subscribed to meeting", message.meetings);
            break;

          case "pong":
            // Keepalive acknowledged
            break;

          case "error":
            console.error("WebSocket error:", message.message);
            onError?.(message.message);
            break;
        }
      } catch (error) {
        console.error("Failed to parse WebSocket message:", error);
      }
    },
    [nativeId, addLiveTranscript, setBotStatus, onTranscript, onStatusChange, onError]
  );

  // Bootstrap transcripts from REST API before connecting
  const bootstrapFromRest = useCallback(async () => {
    if (bootstrappedRef.current) return;

    try {
      console.log(`[VexaWebSocket] Bootstrapping transcripts from REST API: ${platform}/${nativeId}`);
      const segments = await vexaAPI.getTranscripts(platform, nativeId);
      console.log(`[VexaWebSocket] Bootstrapped ${segments.length} segments from REST API`);
      
      // Bootstrap the live transcripts store
      bootstrapLiveTranscripts(segments);
      bootstrappedRef.current = true;
    } catch (error) {
      console.error("[VexaWebSocket] Bootstrap from REST API failed:", error);
      // Continue anyway - WebSocket will provide segments
      bootstrappedRef.current = true;
    }
  }, [platform, nativeId, bootstrapLiveTranscripts]);

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    cleanup();
    shouldReconnectRef.current = true;
    setConnectionState(true, false);

    // Bootstrap transcripts from REST API before connecting
    await bootstrapFromRest();

    const config = await fetchConfig();
    const wsUrl = buildWsUrl(config.wsUrl, config.authToken);
    console.log("WebSocket: Connecting to", wsUrl.replace(/api_key=([^&]+)/, "api_key=***"));

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("WebSocket: Connected");
        setConnectionState(false, true);

        // Subscribe to meeting
        ws.send(
          JSON.stringify({
            action: "subscribe",
            meetings: [{ platform, native_id: nativeId }],
          })
        );

        // Start ping interval
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "ping" }));
          }
        }, PING_INTERVAL);
      };

      ws.onmessage = handleMessage;

      ws.onerror = (event) => {
        console.error("WebSocket error:", event);
        setConnectionState(false, false, "Connection error");
        onError?.("WebSocket connection error");
      };

      ws.onclose = (event) => {
        console.log("WebSocket: Closed", event.code, event.reason);
        setConnectionState(false, false);

        // Cleanup ping interval
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }

        // Auto-reconnect if not intentionally closed
        if (shouldReconnectRef.current && event.code !== 1000) {
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log("WebSocket: Attempting reconnection...");
            connect();
          }, RECONNECT_DELAY);
        }
      };
    } catch (error) {
      console.error("Failed to create WebSocket:", error);
      setConnectionState(false, false, (error as Error).message);
      onError?.((error as Error).message);
    }
  }, [platform, nativeId, handleMessage, cleanup, setConnectionState, onError, bootstrapFromRest]);

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;
    cleanup();
    setConnectionState(false, false);
    bootstrappedRef.current = false; // Reset bootstrap flag on disconnect
  }, [cleanup, setConnectionState]);

  // Auto-connect on mount if enabled
  useEffect(() => {
    if (autoConnect && platform && nativeId) {
      connect();
    }

    return () => {
      shouldReconnectRef.current = false;
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConnect, platform, nativeId]);

  return {
    isConnecting,
    isConnected,
    error: connectionError,
    connect,
    disconnect,
  };
}
