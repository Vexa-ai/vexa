import type { WsTransport } from "@vexa/dash-ws";

/**
 * The browser `WsTransport` adapter — the real-socket boundary `@vexa/dash-ws` drives.
 *
 * `@vexa/dash-ws` is deliberately socket-agnostic: it owns the subscribe/ping/dispatch protocol and
 * calls a `WsTransport` for the actual connection. In tests that boundary is `createFakeWsTransport`;
 * in the browser it is THIS — a thin wrapper over the native `WebSocket`, so the only place a real
 * socket exists is the composition root, never the brick. One transport per connection (created fresh
 * by the `wsClientFactory` for each meeting).
 */
export function createBrowserWsTransport(): WsTransport {
  let socket: WebSocket | null = null;
  // The client registers its handlers BEFORE calling connect() (createWsClient.start order), so the
  // transport must STORE them and wire them onto the socket when connect() creates it — otherwise a
  // fast (localhost) open fires before any listener exists and the subscribe is never sent.
  let onMessageCb: ((data: string) => void) | null = null;
  let onOpenCb: (() => void) | null = null;
  let onCloseCb: (() => void) | null = null;

  return {
    connect(url: string) {
      socket = new WebSocket(url);
      socket.addEventListener("message", (ev) => onMessageCb?.(String(ev.data)));
      socket.addEventListener("open", () => onOpenCb?.());
      socket.addEventListener("close", () => onCloseCb?.());
    },
    send(msg: string) {
      socket?.send(msg);
    },
    onMessage(cb: (data: string) => void) {
      onMessageCb = cb;
    },
    onOpen(cb: () => void) {
      onOpenCb = cb;
    },
    onClose(cb: () => void) {
      onCloseCb = cb;
    },
    close() {
      socket?.close();
      socket = null;
    },
  };
}
