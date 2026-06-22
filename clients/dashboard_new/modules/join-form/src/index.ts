/**
 * @vexa/dash-join-form — the start-bot form VIEW brick (front door).
 *
 * A presentational React component (props in, DOM out): platform select + meeting URL/native-id input
 * + bot name → `onSubmit` a `CreateBotRequest`. No store, no fetch, no websocket — data is injected.
 * Typed by @vexa/dash-contracts (`Platform`). The URL→(platform, native id) parse is exported too so
 * the same logic can be reused/tested in isolation.
 */
export { JoinForm } from "./JoinForm.js";
export type { JoinFormProps } from "./JoinForm.js";
export { parseMeetingInput } from "./parse-meeting-input.js";
export type { ParsedMeetingInput } from "./parse-meeting-input.js";
export type { CreateBotRequest, Platform } from "./types.js";
