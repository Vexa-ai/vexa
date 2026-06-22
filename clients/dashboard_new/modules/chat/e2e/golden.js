/**
 * golden.js — the single source of truth for the props the L4 harness mounts and asserts on.
 *
 * Shared by BOTH sides of the L4 gate so they can never drift:
 *   • the fixture entry (chat-entry.tsx) imports these and mounts <ChatPanel messages={GOLDEN_MESSAGES}/>
 *   • the spec (chat-render.spec.ts) imports these and asserts the DOM rendered exactly this text
 *
 * Two golden ChatMessage props, shaped per @vexa/dash-contracts ws.v1 ChatMessageFrame floor
 * (sender + text) plus the optional display fields the bubble paints (timestamp, is_from_bot):
 * one human message, one bot message.
 */
export const GOLDEN_MESSAGES = [
  {
    sender: "Alice",
    text: "is the bot recording this?",
    timestamp: Date.UTC(2026, 5, 22, 10, 0, 0),
    is_from_bot: false,
  },
  {
    sender: "Vexa Bot",
    text: "yes, transcription is live",
    timestamp: Date.UTC(2026, 5, 22, 10, 0, 5),
    is_from_bot: true,
  },
];

/** The exact sender text each bubble must show, in order. */
export const GOLDEN_SENDERS = GOLDEN_MESSAGES.map((m) => m.sender);

/** The exact body text each bubble must show, in order. */
export const GOLDEN_TEXTS = GOLDEN_MESSAGES.map((m) => m.text);
