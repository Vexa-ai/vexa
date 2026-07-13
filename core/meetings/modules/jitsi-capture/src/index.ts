/**
 * @vexa/jitsi-capture — Jitsi Meet's contribution to the mixed lane.
 *
 * Like Zoom/Teams, Jitsi delivers one mixed audio stream (captured by
 * @vexa/mixed-capture-core); this module provides the WHO + chat signals:
 *   - createJitsiSpeakers: watches the app's dominant-speaker state (redux
 *     primary, DOM fallback) → a mixed-capture.v1 `hint` (kind 'dom-active').
 *   - createJitsiChat: reads conference chat (redux primary — the panel need
 *     not be open; DOM fallback) + sendJitsiChatMessage over the app's own API.
 */
export {
  createJitsiSpeakers,
  jitsiDominantTileSelectors,
  jitsiTileNameSelectors,
} from "./jitsi-speakers.js";
export type { JitsiSpeakers, JitsiSpeakersOptions } from "./jitsi-speakers.js";
export {
  createJitsiChat,
  sendJitsiChatMessage,
  jitsiChatContainerSelectors,
  jitsiChatMessageSelectors,
  jitsiChatSenderSelectors,
  jitsiChatTextSelectors,
} from "./jitsi-chat.js";
export type { JitsiChat, JitsiChatMessage, JitsiChatOptions } from "./jitsi-chat.js";
