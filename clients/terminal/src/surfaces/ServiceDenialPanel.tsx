"use client";
/** ServiceDenialPanel — the in-flow rendering of a refused Join.
 *
 *  A panel, not a transient error line: a paywall is a thing the customer has to ACT on, and the
 *  one-line red `⚠ …` the join surfaces use for "bad link" both loses the fix and (on the sidebar)
 *  clears itself after 5s. Styling follows the terminal's existing inline-notice idiom — CSS-var
 *  semantic colours, tinted wash + border, `Icon` from the ui-kit (cf. `workbench/OpsNotice.tsx`
 *  and `canvas/MeetingHealthBanner.tsx`).
 *
 *  Twin of `services/dashboard/src/components/join/service-denial-panel.tsx`; the WORDS live in
 *  `surfaces/serviceDenial.ts` and are shared with it, only the skin differs.
 */
import { Icon } from "../ui-kit";
import { denialActionUrl, type ServiceDenialKind, type ServiceDenialPresentation } from "./serviceDenial";

/** `--danger` is destructive/errors ONLY and `--warn` is attention-not-error (globals.css §semantic
 *  set), so a paywall — the account is fine, it needs money — is warn, and only a reason this build
 *  has never heard of (a bug) is danger. */
const TONE: Record<ServiceDenialKind, { fg: string; bg: string; icon: string }> = {
  paywall: { fg: "var(--warn)", bg: "var(--warnbg)", icon: "zap" },
  setup: { fg: "var(--warn)", bg: "var(--warnbg)", icon: "gear" },
  limit: { fg: "var(--accent)", bg: "var(--accentbg)", icon: "info" },
  retryable: { fg: "var(--t2)", bg: "var(--panel2)", icon: "refresh" },
  unknown: { fg: "var(--danger)", bg: "var(--dangerbg)", icon: "alert" },
};

export function ServiceDenialPanel({
  presentation,
  onRetry,
}: {
  presentation: ServiceDenialPresentation;
  onRetry?: () => void;
}) {
  const { kind, title, body, action, retryable, reason } = presentation;
  const tone = TONE[kind];

  return (
    <div
      role="alert"
      data-testid="service-denial-panel"
      data-denial-kind={kind}
      data-denial-reason={reason}
      style={{
        display: "flex", flexDirection: "column", gap: 5,
        marginTop: 6, padding: "8px 10px", borderRadius: 7,
        background: tone.bg,
        border: `1px solid color-mix(in srgb, ${tone.fg} 40%, transparent)`,
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: tone.fg }}>
        <Icon name={tone.icon} size={12} style={{ color: tone.fg, flex: "none" }} />
        {title}
      </span>
      <span style={{ fontSize: 11, color: "var(--t2)", lineHeight: 1.5 }}>{body}</span>
      {(action || (retryable && onRetry)) && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
          {action && (
            <a
              href={denialActionUrl(action.href)}
              target="_blank"
              rel="noreferrer"
              style={{
                background: "var(--accent)", color: "var(--on-accent)", borderRadius: 6,
                padding: "4px 10px", fontSize: 11.5, fontWeight: 600, textDecoration: "none",
              }}
            >
              {action.label}
            </a>
          )}
          {retryable && onRetry && (
            <button
              type="button"
              onClick={onRetry}
              style={{
                background: "transparent", color: "var(--t2)", border: "1px solid var(--line2)",
                borderRadius: 6, padding: "4px 10px", fontSize: 11.5, fontWeight: 600, cursor: "pointer",
              }}
            >
              Try again
            </button>
          )}
        </div>
      )}
    </div>
  );
}
