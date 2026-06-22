/**
 * fakes.ts — the deterministic in-memory transport.
 *
 * `createFakeWsTransport()` returns a `WsTransport` plus two test affordances:
 *   • `.emit(frame)`  — deliver a server → client frame to the registered onMessage cb. Accepts an
 *                       object (JSON-stringified for you) or a raw string (to test malformed input).
 *   • `.sent[]`       — every payload the client handed to `send()`, in order (subscribe + pings).
 *
 * `.fireOpen()` / `.fireClose()` drive the open/close handlers so a test can step the lifecycle by
 * hand. No real socket, no timers, no globals — the test owns the clock.
 */
import type { WsTransport } from "./ports.js";

export interface FakeWsTransport extends WsTransport {
  /** Deliver a frame to the onMessage cb. Object → JSON string; string → passed through verbatim. */
  emit(frame: unknown): void;
  /** Trigger the registered onOpen handler. */
  fireOpen(): void;
  /** Trigger the registered onClose handler. */
  fireClose(): void;
  /** Every payload passed to send(), in order. */
  readonly sent: string[];
  /** The url the client connected to (with the appended ?api_key=…), or null if never connected. */
  readonly connectedUrl: string | null;
  /** Whether close() has been called. */
  readonly closed: boolean;
}

export function createFakeWsTransport(): FakeWsTransport {
  const sent: string[] = [];
  let onMessageCb: ((data: string) => void) | null = null;
  let onOpenCb: (() => void) | null = null;
  let onCloseCb: (() => void) | null = null;
  let connectedUrl: string | null = null;
  let closed = false;

  return {
    // ── WsTransport surface ──────────────────────────────────────────────────────────────────────
    connect(url: string) {
      connectedUrl = url;
    },
    send(msg: string) {
      sent.push(msg);
    },
    onMessage(cb) {
      onMessageCb = cb;
    },
    onOpen(cb) {
      onOpenCb = cb;
    },
    onClose(cb) {
      onCloseCb = cb;
    },
    close() {
      closed = true;
    },

    // ── test affordances ─────────────────────────────────────────────────────────────────────────
    emit(frame: unknown) {
      const data = typeof frame === "string" ? frame : JSON.stringify(frame);
      onMessageCb?.(data);
    },
    fireOpen() {
      onOpenCb?.();
    },
    fireClose() {
      onCloseCb?.();
    },
    get sent() {
      return sent;
    },
    get connectedUrl() {
      return connectedUrl;
    },
    get closed() {
      return closed;
    },
  };
}
