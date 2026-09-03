"use client";
/** useGatewayWS — ONE shared WebSocket to the gateway `/ws`, carrying user-scoped meeting-status frames.
 *
 *  The gateway resolves the user_id from the api_key at connect (Track ①) and auto-subscribes the socket
 *  to `u:{user_id}:meetings` — so the client just opens the socket; no `subscribe` frame is sent. Each
 *  frame is the `meeting.status` data message (ws.v1). Per §C.1 the user-channel frame is additive: it
 *  carries the flat fields `{meeting_id, native, status, when}` AND may keep the legacy nested shape
 *  `{meeting:{id,native_id}, payload:{status}, ts}` — we read either.
 *
 *  Transport: the browser connects KEYLESS to same-origin `/ws`. The custom Next server (server.mjs)
 *  intercepts the upgrade, opens a server-side socket to the gateway with the `x-api-key` header, and
 *  pipes frames — so the api_key never appears in any client-visible URL. Reconnect with capped
 *  backoff; the consumer re-seeds via one `GET /api/meetings` snapshot on each (re)connect.
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
/** How long a socket must stay open before it counts as working and clears the backoff. */
const STABLE_MS = 10000;
let stableTimer: ReturnType<typeof setTimeout> | null = null;
/** The gateway REJECTED our credential (close 4401). Not a transport failure — retrying cannot fix
 *  it, and retrying is exactly what this module used to do, forever, at one attempt per second.
 *
 *  Measured on the founder's live session 2026-09-02: his browser held a `vexa-token` cookie whose
 *  API token no longer existed (`select count(*) from api_tokens where user_id=57` → 0), the WS
 *  proxy dutifully presented it on every upgrade, and the gateway correctly answered
 *  `invalid_api_key` + close 4401. The socket was never being DROPPED — it was being REFUSED, and
 *  the client would not take no for an answer: ~3 upgrades a second, indefinitely, plus a
 *  `GET /api/meetings` re-seed per reconnect.
 *
 *  4401 says "your key is bad", so we stop and say so. 4503 says "the auth hop is down" and IS
 *  retryable — the gateway distinguishes them deliberately (app.py: AuthUnavailable → 4503, never
 *  4401, precisely so a valid key is not told it is invalid), and this must honour that. */
let authRejected = false;
const WS_UNAUTHORIZED = 4401;
const listeners = new Set<Listener>();
const connListeners = new Set<ConnListener>();
let connected = false;

function setConnected(v: boolean) {
  if (connected === v) return;
  connected = v;
  connListeners.forEach((f) => f(v));
}

/** Normalise either the flat (§C.1) or legacy-nested (§0.2) meeting.status shape to a flat frame.
 *  Exported (additive — no runtime behavior change) so the contract-conformance test can pin it to
 *  the ws.v1 golden frame. */
export function parseFrame(data: unknown): MeetingStatusFrame | null {
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

/** Open the ONE shared socket. The `ws`/`starting` guards make this idempotent, so the many
 *  components mounting `useGatewayWS` collapse onto a single socket no matter how often they call it.
 *  Connects KEYLESS to same-origin `/ws` — the custom server (server.mjs) injects the api_key
 *  server-side on the upgrade to the gateway. */
function connect() {
  if (typeof window === "undefined" || ws || starting) return;
  if (listeners.size === 0) return;            // only (re)connect when someone is listening
  if (authRejected) return;                    // a refused key is not retried until it can change
  starting = true;
  try {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const sock = new WebSocket(`${proto}//${location.host}/ws`);
    ws = sock;
    sock.onopen = () => {
      setConnected(true);
      // BACKOFF RESETS ON A CONNECTION THAT PROVED ITSELF, NEVER ON `onopen`.
      //
      // Resetting `retry` here made the backoff a no-op against the one failure mode it exists for:
      // a socket that OPENS and then closes immediately. open → retry=0 → close → reconnect in
      // 2^0 = 1s → open → retry=0 → … forever, at one reconnect per second, with no growth.
      //
      // Measured on the founder's live session 2026-09-02: the gateway was accepting and closing
      // /ws about once a second, and because `liveMeetings` re-seeds with a `GET /api/meetings`
      // snapshot on every (re)connect, one idle browser made 519 calls in three minutes and the
      // store notified its subscribers on every connectedness flip — which is what made the rail
      // flicker meetings in and out under him.
      //
      // A connection that survives STABLE_MS is a working connection, and only that clears the
      // backoff. A flapping one now walks 1s → 2s → 4s … → 30s as intended, so a server-side close
      // loop degrades to a slow retry instead of a client-side storm.
      if (stableTimer) clearTimeout(stableTimer);
      stableTimer = setTimeout(() => { retry = 0; stableTimer = null; }, STABLE_MS);
    };
    sock.onmessage = (m) => {
      let data: unknown;
      try { data = JSON.parse(typeof m.data === "string" ? m.data : ""); } catch { return; }
      const f = parseFrame(data);
      if (f) listeners.forEach((fn) => fn(f));
    };
    sock.onclose = (e) => {
      if (ws === sock) ws = null;
      // the connection did not last: whatever progress it made toward "stable" does not count
      if (stableTimer) { clearTimeout(stableTimer); stableTimer = null; }
      setConnected(false);
      if (e?.code === WS_UNAUTHORIZED) {
        // A refused credential. Reconnecting re-presents the SAME bad key, so the loop can only
        // ever hammer. Stop, and say it once — silence here reads as "no meetings are happening".
        authRejected = true;
        console.error(
          "gateway /ws refused this session's credential (close 4401) — live meeting updates are "
          + "off until you sign in again. Not retrying: the same key would be presented each time.",
        );
        return;
      }
      scheduleReconnect();
    };
    sock.onerror = () => { try { sock.close(); } catch { /* noop */ } };
  } catch {
    ws = null;
    scheduleReconnect();
  } finally {
    starting = false;
  }
}

function clearReconnect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
}

function scheduleReconnect() {
  if (reconnectTimer || listeners.size === 0) return;
  const delay = Math.min(1000 * 2 ** retry, 30000);  // 1s, 2s, 4s … capped at 30s
  retry += 1;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, delay);
}

/** Subscribe to meeting.status frames. Returns an unsubscribe fn; opens the socket on first subscriber. */
export function onMeetingStatus(fn: Listener): () => void {
  listeners.add(fn);
  connect();
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) {
      clearReconnect();                          // kill any pending reconnect on the last unsubscribe
      if (stableTimer) { clearTimeout(stableTimer); stableTimer = null; }
      retry = 0;
      // The latch clears with the last listener, so the next mount — which is what a sign-in
      // produces — gets one clean attempt with whatever cookie is current. It never clears on a
      // timer, because nothing about waiting makes a rejected key valid.
      authRejected = false;
      if (ws) { try { ws.close(); } catch { /* noop */ } ws = null; }
    }
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
