"use client";
/** ServiceDenialPanel — the in-flow rendering of a refused Join.
 *
 *  A panel, not a transient error line: a refusal is a thing the customer has to ACT on, and the
 *  one-line red `⚠ …` the join surfaces use for "bad link" both loses the fix and (on the sidebar)
 *  clears itself after 5s. Styling follows the terminal's existing inline-notice idiom — CSS-var
 *  semantic colours, tinted wash + border, `Icon` from the ui-kit (cf. `workbench/OpsNotice.tsx`
 *  and `canvas/MeetingHealthBanner.tsx`).
 *
 *  Every WORD here comes off the wire (`surfaces/serviceDenial.ts`): the headline the decider
 *  authored, the `HTTP <status> <code>` line under it, and its `action_url` shown verbatim as the
 *  place to resolve it. The panel names no reason and holds no copy, so a refusal this build has
 *  never seen renders exactly as well as one it has.
 */
import { Icon } from "../ui-kit";
import type { ServiceDenialPresentation } from "./serviceDenial";

export function ServiceDenialPanel({
  presentation,
  onRetry,
}: {
  presentation: ServiceDenialPresentation;
  onRetry?: () => void;
}) {
  const { headline, detail, actionUrl, reason, code } = presentation;
  // `--danger` is destructive/errors ONLY and `--warn` is attention-not-error (globals.css
  // §semantic set). A decision is not an error: the account is intact and something can be done.
  const fg = "var(--warn)";

  return (
    <div
      role="alert"
      data-testid="service-denial-panel"
      data-denial-code={code}
      data-denial-reason={reason}
      style={{
        display: "flex", flexDirection: "column", gap: 5,
        marginTop: 6, padding: "8px 10px", borderRadius: 7,
        background: "var(--warnbg)",
        border: `1px solid color-mix(in srgb, ${fg} 40%, transparent)`,
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: fg, lineHeight: 1.45 }}>
        <Icon name="alert" size={12} style={{ color: fg, flex: "none" }} />
        {headline}
      </span>
      <span style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--mono, monospace)" }}>{detail}</span>
      {(actionUrl || onRetry) && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2, flexWrap: "wrap" }}>
          {actionUrl && (
            <a
              href={actionUrl}
              target="_blank"
              rel="noreferrer"
              style={{
                background: "var(--accent)", color: "var(--on-accent)", borderRadius: 6,
                padding: "4px 10px", fontSize: 11.5, fontWeight: 600, textDecoration: "none",
                wordBreak: "break-all",
              }}
            >
              {actionUrl}
            </a>
          )}
          {onRetry && (
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
