/**
 * ports.ts — the transport seam.
 *
 * `createWsClient` never touches a real `WebSocket`. It drives a `WsTransport`, an injected
 * boundary that owns the socket lifecycle (connect/send/close) and the four event taps the client
 * subscribes to. In the browser this is a thin wrapper over the native `WebSocket`; in tests it is
 * `createFakeWsTransport` (fakes.ts), which lets a test deliver frames and inspect what was sent.
 *
 * This indirection is what makes the brick deterministic: no global `WebSocket`, no jsdom, no timers
 * the test can't control.
 */
export interface WsTransport {
  /** Open a connection to `url` (the client appends `?api_key=…` before calling this). */
  connect(url: string): void;
  /** Send a frame to the server (the client serializes to a JSON string first). */
  send(msg: string): void;
  /** Register the handler for inbound frames; `data` is the raw JSON string off the wire. */
  onMessage(cb: (data: string) => void): void;
  /** Register the open handler (the client subscribes + starts pinging here). */
  onOpen(cb: () => void): void;
  /** Register the close handler. */
  onClose(cb: () => void): void;
  /** Tear the connection down (also stops the client's ping interval). */
  close(): void;
}
