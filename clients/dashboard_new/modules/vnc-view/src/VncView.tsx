/**
 * VncView — the #5 per-bot noVNC viewer (presentational).
 *
 * Embeds the per-bot noVNC session in an <iframe>. The bot container runs noVNC; the gateway will
 * route `/b/{id}/vnc/*` to it, so the parent composes the full URL (e.g.
 * `/b/{token}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&path=b/{token}/vnc/websockify`)
 * and injects it as `vncUrl`. This brick is props-in / DOM-out: NO store, NO fetch, NO ws — the URL is
 * always supplied by the caller. Behavior is the vendored dashboard's `browser-session-view` /
 * `meetings/[id]` iframe, carved clean.
 *
 *   • vncUrl non-empty → render the live <iframe> (the noVNC viewer).
 *   • vncUrl empty     → render the loading/placeholder state (URL not ready yet).
 *
 * `allow="clipboard-read; clipboard-write"` mirrors the vendored viewer so noVNC clipboard sync works.
 */
import * as React from "react";

export interface VncViewProps {
  /**
   * The fully-composed per-bot noVNC URL (gateway `/b/{id}/vnc/...`). Empty string while the bot /
   * session token is still resolving — that drives the placeholder.
   */
  vncUrl: string;
  /** Optional accessible title for the embedded viewer iframe. */
  title?: string;
  /** Optional placeholder text shown while `vncUrl` is empty. */
  placeholderText?: string;
}

/**
 * The per-bot VNC viewer. Mount with a golden `vncUrl` → an <iframe src={vncUrl}> renders; mount with
 * an empty `vncUrl` → the placeholder renders. Pure presentational — identical output for identical
 * props.
 */
export function VncView(props: VncViewProps): React.ReactElement {
  const { vncUrl, title = "Bot VNC viewer", placeholderText = "Connecting to bot…" } = props;

  if (!vncUrl) {
    return (
      <div
        data-testid="vnc-placeholder"
        role="status"
        aria-live="polite"
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <span data-testid="vnc-placeholder-text">{placeholderText}</span>
      </div>
    );
  }

  return (
    <iframe
      data-testid="vnc-iframe"
      src={vncUrl}
      title={title}
      allow="clipboard-read; clipboard-write"
      style={{ width: "100%", height: "100%", border: 0 }}
    />
  );
}

export default VncView;
