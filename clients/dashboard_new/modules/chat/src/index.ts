/**
 * @vexa/dash-chat — the presentational dashboard chat-panel VIEW brick.
 *
 * One front door. Exports the `ChatPanel` React component (props in, DOM out — no store, no fetch, no
 * ws) and its prop types. The message shape is anchored on the @vexa/dash-contracts ws.v1
 * `ChatMessageFrame` floor; see `ChatPanel.tsx` and README.md for the props contract.
 */
export { ChatPanel } from "./ChatPanel.js";
export type { ChatMessage, ChatPanelProps } from "./ChatPanel.js";
