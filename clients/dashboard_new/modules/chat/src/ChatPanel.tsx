/**
 * ChatPanel.tsx — the presentational dashboard chat view.
 *
 * Props in, DOM out. Given `{ messages }`, it renders one bubble per message — sender, text, and a
 * formatted time — newest at the bottom (the order they're passed). There is NO store, NO fetch, NO
 * websocket here: the vendored chat-panel.tsx pulled messages off `useMeetingsStore` and bootstrapped
 * them over REST; this clean brick takes the already-fetched array as a prop so it's pure, typed, and
 * trivially testable (mount over golden props → assert DOM).
 *
 * The message shape is anchored on the @vexa/dash-contracts ws.v1 `ChatMessageFrame` floor
 * (`sender` + `text`) and extended — additively, the ws.v1 way — with the optional display fields the
 * view paints when present (`timestamp` for the time, `is_from_bot` for bubble alignment/styling).
 */
import * as React from "react";
// type-only: resolves to the built @vexa/dash-contracts .d.ts (the workspace-linked package front
// door), so the contract types are consumed without dragging the contracts' ajv runtime into this
// brick's compile. Same pattern the proven @vexa/dash-ws sibling uses.
import type { ChatMessageFrame } from "@vexa/dash-contracts";

/**
 * One chat message the panel renders. Anchored on the ws.v1 `ChatMessageFrame` floor
 * (`sender`, `text`) from @vexa/dash-contracts, plus the optional display fields the bubble uses:
 *   • `timestamp` — Unix ms; rendered as a localized HH:MM when present.
 *   • `is_from_bot` — aligns/styles the bubble (bot messages on the right) when present.
 */
export interface ChatMessage extends Pick<ChatMessageFrame, "sender" | "text"> {
  /** Unix epoch milliseconds. When present, rendered as a short localized time. */
  timestamp?: number;
  /** True for messages the bot itself posted (aligns the bubble to the right). */
  is_from_bot?: boolean;
}

export interface ChatPanelProps {
  /** The chat messages to render, in display order (oldest → newest). Injected; never fetched here. */
  messages: ChatMessage[];
  /** When true, a small "live" hint is shown under the list. Optional, default false. */
  isActive?: boolean;
}

/** Format a Unix-ms timestamp as a short localized time (e.g. "10:00 AM"). */
function formatTimestamp(ts: number): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ChatBubble({ message }: { message: ChatMessage }): React.ReactElement {
  const sender = message.sender ?? "Unknown";
  const time =
    typeof message.timestamp === "number" ? formatTimestamp(message.timestamp) : "";
  return (
    <div
      className="dash-chat-bubble"
      data-from-bot={message.is_from_bot ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "2px",
        alignItems: message.is_from_bot ? "flex-end" : "flex-start",
      }}
    >
      <span className="dash-chat-meta" style={{ fontSize: "10px", opacity: 0.7, padding: "0 4px" }}>
        <span className="dash-chat-sender">{sender}</span>
        {time ? (
          <>
            {" · "}
            <span className="dash-chat-time">{time}</span>
          </>
        ) : null}
      </span>
      <div
        className="dash-chat-text"
        style={{
          borderRadius: "8px",
          padding: "6px 12px",
          fontSize: "14px",
          maxWidth: "85%",
          wordBreak: "break-word",
          background: message.is_from_bot
            ? "var(--primary, #2563eb)"
            : "var(--muted, #f1f5f9)",
          color: message.is_from_bot
            ? "var(--primary-foreground, #ffffff)"
            : "var(--foreground, #0f172a)",
        }}
      >
        {message.text}
      </div>
    </div>
  );
}

/**
 * ChatPanel — render a list of in-meeting chat messages. Pure & presentational.
 */
export function ChatPanel({ messages, isActive = false }: ChatPanelProps): React.ReactElement {
  return (
    <section className="dash-chat" aria-label="Chat">
      <header className="dash-chat-header" style={{ fontWeight: 600, marginBottom: "8px" }}>
        Chat{messages.length > 0 ? ` (${messages.length})` : ""}
      </header>
      {messages.length === 0 ? (
        <p
          className="dash-chat-empty"
          style={{ fontSize: "14px", opacity: 0.6, fontStyle: "italic", textAlign: "center", padding: "16px 0" }}
        >
          No chat messages yet
        </p>
      ) : (
        <div
          className="dash-chat-list"
          style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "300px", overflowY: "auto" }}
        >
          {messages.map((msg, i) => (
            <ChatBubble key={`${msg.timestamp ?? "t"}-${i}`} message={msg} />
          ))}
        </div>
      )}
      {isActive ? (
        <p
          className="dash-chat-live"
          style={{ fontSize: "10px", opacity: 0.6, marginTop: "8px", textAlign: "center" }}
        >
          Live — messages appear in real time
        </p>
      ) : null}
    </section>
  );
}
