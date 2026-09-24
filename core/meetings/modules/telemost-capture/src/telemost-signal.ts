/**
 * Yandex Telemost media-engine signal tap — who is speaking, from the call's own signalling.
 *
 * Pure browser code (no Node, no Playwright, no cross-file imports — the bot bundles this file
 * standalone). Installed at DOCUMENT START in every frame (the bot's init script, beside the WebRTC
 * audio hook): it wraps `WebSocket` so the call frame's connection to the media engine (goloom) is
 * observed from its first message. Observing only — messages are never altered or delayed.
 *
 * The engine tells every participant, over that socket:
 *   - `upsertDescription` / `updateDescription` → `description[]` of `{ id, meta: { name }, sendAudio }`
 *     — the participant roster, id → display name, and whether that participant's client is
 *     transmitting audio right now (its own voice gate: on ~1 s before the tile outline, off with it);
 *   - `removeDescription` → participants that left;
 *   - `slotsConfig` → `slots[]`, each naming the participant it shows (`participantVideoByMid` /
 *     `participant` / … → `participantId`) with the server's `vad` flag. The engine re-sends the
 *     configuration on every change of that flag, in the grid and with a screen shared alike — the
 *     tiles' speaking outline is drawn from exactly this flag.
 * A layout can carry no participant slot at all (a share with no room for a strip): then neither the
 * flag nor an outline can name anyone, and `sendAudio` on the roster channel is the one signal left.
 * Screen-share and self-view slots carry no speaker and are skipped.
 */

export interface TelemostSignalState {
  /** participantId → display name. */
  names: Map<string, string>;
  /** participantIds whose slot currently carries `vad: true`. */
  speaking: Set<string>;
  /** participantIds whose client is transmitting audio (`sendAudio: true` on the roster channel). */
  sending: Set<string>;
  /** Participant slots (a speaker each) in the latest slot configuration. */
  participantSlots: number;
  /** Engine messages observed, and how many of them were slot configurations. */
  messages: number;
  slotConfigs: number;
  /** Slot observations with `vad: true`, and the speaking ids no roster entry has named (yet). */
  vadSlots: number;
  unnamed: Set<string>;
  /** Whether the latest slot configuration carries a screen-share slot. */
  sharing: boolean;
}

const STATE_KEY = "__vexaTelemostSignal";

/** The tap's state, or null when no tap is installed in this frame. */
export function telemostSignalState(): TelemostSignalState | null {
  return ((globalThis as any)[STATE_KEY] as TelemostSignalState | undefined) ?? null;
}

export function emptyTelemostSignalState(): TelemostSignalState {
  return { names: new Map(), speaking: new Set(), sending: new Set(), participantSlots: 0, messages: 0, slotConfigs: 0, vadSlots: 0, unnamed: new Set(), sharing: false };
}

/** Every `{ id, meta: { name }, sendAudio }` roster entry anywhere in a message — the roster rides
 *  several verbs (`upsertDescription`, `updateDescription`, the join handshake), so it is found by its
 *  shape. `sendAudio` is read wherever the entry carries it (top level or under `meta`). */
function collectRoster(node: any, state: TelemostSignalState, depth = 0): void {
  if (!node || typeof node !== "object" || depth > 6) return;
  if (Array.isArray(node)) { for (const x of node) collectRoster(x, state, depth + 1); return; }
  const name = typeof node.meta?.name === "string" ? node.meta.name.trim() : "";
  if (typeof node.id === "string" && name) {
    state.names.set(node.id, name);
    const sending = typeof node.sendAudio === "boolean" ? node.sendAudio
      : typeof node.meta?.sendAudio === "boolean" ? node.meta.sendAudio : null;
    if (sending === true) state.sending.add(node.id);
    else if (sending === false) state.sending.delete(node.id);
  }
  for (const k of Object.keys(node)) {
    const v = node[k];
    if (v && typeof v === "object") collectRoster(v, state, depth + 1);
  }
}

/** Participant ids a `removeDescription` names, whichever shape it carries them in. */
function removedIds(m: any): string[] {
  const r = m?.removeDescription;
  if (!r) return [];
  const ids: string[] = [];
  for (const key of ["description", "participantIds", "ids"]) {
    const list = r[key];
    if (!Array.isArray(list)) continue;
    for (const x of list) {
      const id = typeof x === "string" ? x : typeof x?.id === "string" ? x.id : "";
      if (id) ids.push(id);
    }
  }
  return ids;
}

/** The participant a slot shows, or "" for a slot with no speaker. Screen-share and self-view slots
 *  are not speakers; any other slot names its participant under some object key — `participantVideoByMid`
 *  with a camera on, plain `participant` without one. */
function slotParticipant(slot: any): string {
  if (!slot || typeof slot !== "object" || slot.selfView || slot.participantScreenSharingByMid) return "";
  for (const k of Object.keys(slot)) {
    const v = slot[k];
    if (v && typeof v === "object" && typeof v.participantId === "string") return v.participantId;
  }
  return typeof slot.participantId === "string" ? slot.participantId : "";
}

/** Apply one engine message to the state. Exported for the unit test; the tap calls it. */
export function applyTelemostSignal(state: TelemostSignalState, raw: unknown): void {
  if (typeof raw !== "string") return;
  let m: any;
  try { m = JSON.parse(raw); } catch { return; }
  if (!m || typeof m !== "object") return;
  state.messages++;
  collectRoster(m, state);
  for (const id of removedIds(m)) { state.names.delete(id); state.speaking.delete(id); state.sending.delete(id); }
  for (const id of state.unnamed) if (state.names.has(id)) state.unnamed.delete(id);
  const cfg = m.slotsConfig;
  if (cfg && typeof cfg === "object") {
    state.slotConfigs++;
    const speaking = new Set<string>();
    let sharing = false;
    let participantSlots = 0;
    for (const list of [cfg.slots, cfg.audioSlots, cfg.videoSlots]) {
      if (!Array.isArray(list)) continue;
      for (const slot of list) {
        if (typeof slot?.participantScreenSharingByMid?.participantId === "string") sharing = true;
        const id = slotParticipant(slot);
        if (!id) continue;
        participantSlots++;
        if (slot.vad !== true) continue;
        speaking.add(id);
        state.vadSlots++;
        if (!state.names.has(id)) state.unnamed.add(id);
      }
    }
    state.speaking = speaking;
    state.sharing = sharing;
    state.participantSlots = participantSlots;
  }
}

/**
 * Wrap `WebSocket` in this frame so the media engine's socket is observed. Idempotent; returns
 * whether it installed. Only sockets whose URL names the engine (`goloom`) are listened to.
 */
export function installTelemostSignalTap(): boolean {
  const w = globalThis as any;
  if (w[STATE_KEY] || typeof w.WebSocket !== "function") return false;
  const state = emptyTelemostSignalState();
  w[STATE_KEY] = state;
  const Orig = w.WebSocket;
  function Tapped(this: unknown, url: string | URL, protocols?: string | string[]) {
    const ws = protocols === undefined ? new Orig(url) : new Orig(url, protocols);
    try {
      if (/goloom/i.test(String(url))) {
        ws.addEventListener("message", (ev: { data: unknown }) => {
          try { applyTelemostSignal(state, ev.data); } catch { /* never break the call */ }
        });
      }
    } catch { /* never break the call */ }
    return ws;
  }
  Tapped.prototype = Orig.prototype;
  for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) (Tapped as any)[k] = Orig[k];
  w.WebSocket = Tapped;
  return true;
}
