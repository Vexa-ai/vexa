"use client";
/** useGatewayWS — ONE shared WebSocket to the gateway `/ws`, carrying user-scoped meeting-status frames.
 *
 *  The gateway resolves the user_id from the api_key at connect (Track ①) and auto-subscribes the socket
 *  to `u:{user_id}:meetings` — so the client just opens the socket; no `subscribe` frame is sent. Each
 *  frame is the `meeting.status` data message (ws.v1). Per §C.1 the user-channel frame is additive: it
 *  carries the flat fields `{meeting_id, native, status, when}` AND may keep the legacy nested shape
 *  `{meeting:{id,native_id}, payload:{status}, ts}` — we read either.
 *
 *  Transport: the api_key is server-side, so we first fetch `/api/ws` for the gateway WS URL (key
 *  embedded server-side, mirroring the SSE proxy), then open it. Reconnect with capped backoff; the
 *  consumer re-seeds via one `GET /api/meetings` snapshot on each (re)connect.
 */

export interface MeetingStatusFrame {
  meeting_id?: number | string;
  native?: string;
  status: string;        // raw meeting-api status
  when?: string;
}

type Listener = (f: MeetingStatusFrame) => void;
type ConnListener = (connected: boolean) => void;

let ws: WebSocket | null = null;
let starting = false;
let retry = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<Listener>();
const connListeners = new Set<ConnListener>();
let connected = false;

function setConnected(v: boolean) {
  if (connected === v) return;
  connected = v;
  connListeners.forEach((f) => f(v));
}

/** Normalise either the flat (§C.1) or legacy-nested (§0.2) meeting.status shape to a flat frame. */
function parseFrame(data: unknown): MeetingStatusFrame | null {
  if (!data || typeof data !== "object") return null;
  const o = data as Record<string, unknown>;
  if (o.type !== "meeting.status") return null;
  const meeting = (o.meeting ?? {}) as Record<string, unknown>;
  const payload = (o.payload ?? {}) as Record<string, unknown>;
  const status = (o.status ?? payload.status) as string | undefined;
  if (!status) return null;
  const meeting_id = (o.meeting_id ?? meeting.id) as number | string | undefined;
  const native = (o.native ?? meeting.native_id) as string | undefined;
  const when = (o.when ?? o.ts) as string | undefined;
  return { meeting_id, native, status, when };
}

async function connect() {
  if (typeof window === "undefined" || ws || starting) return;
  starting = true;
  let url: string;
  try {
    const r = await fetch("/api/ws", { cache: "no-store" });
    url = (await r.json()).url as string;
    if (!url) throw new Error("no ws url");
  } catch {
    starting = false;
    scheduleReconnect();
    return;
  }
  try {
    const sock = new WebSocket(url);
    ws = sock;
    sock.onopen = () => { retry = 0; setConnected(true); };
    sock.onmessage = (m) => {
      let data: unknown;
      try { data = JSON.parse(typeof m.data === "string" ? m.data : ""); } catch { return; }
      const f = parseFrame(data);
      if (f) listeners.forEach((fn) => fn(f));
    };
    sock.onclose = () => { ws = null; setConnected(false); scheduleReconnect(); };
    sock.onerror = () => { try { sock.close(); } catch { /* noop */ } };
  } catch {
    ws = null;
    scheduleReconnect();
  } finally {
    starting = false;
  }
}

function scheduleReconnect() {
  if (reconnectTimer || listeners.size === 0) return;
  const delay = Math.min(1000 * 2 ** retry, 30000);  // 1s, 2s, 4s … capped at 30s
  retry += 1;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; void connect(); }, delay);
}

/** Subscribe to meeting.status frames. Returns an unsubscribe fn; opens the socket on first subscriber. */
export function onMeetingStatus(fn: Listener): () => void {
  listeners.add(fn);
  void connect();
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && ws) { try { ws.close(); } catch { /* noop */ } ws = null; }
  };
}

/** Subscribe to connection-state changes (true once the socket is open). Fires the current state once. */
export function onGatewayWSConnected(fn: ConnListener): () => void {
  connListeners.add(fn);
  fn(connected);
  return () => { connListeners.delete(fn); };
}

export function isGatewayWSConnected(): boolean {
  return connected;
}
