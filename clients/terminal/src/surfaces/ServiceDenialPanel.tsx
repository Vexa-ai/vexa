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
      className="mt-1_5 pt-2 pr-2 pb-2 pl-2 r-md bg-warning-tint" style={{ display: "flex", flexDirection: "column", gap: 5, border: `1px solid color-mix(in srgb, ${fg} 40%, transparent)` }}
    >
      <span className="t-xs fw-600 lh-snug" style={{ display: "flex", alignItems: "center", gap: 6, color: fg }}>
        <Icon name="alert" size={12} style={{ color: fg, flex: "none" }} />
        {headline}
      </span>
      <span className="t-xs c-3 f-mono">{detail}</span>
      {(actionUrl || onRetry) && (
        <div className="mt-0_5" style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {actionUrl && (
            <a
              href={actionUrl}
              target="_blank"
              rel="noreferrer"
              className="bg-accent c-on-accent r-md pt-1 pr-2 pb-1 pl-2 t-xs fw-600" style={{ textDecoration: "none", wordBreak: "break-all" }}
            >
              {actionUrl}
            </a>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="bg-none c-2 bd-strong r-md pt-1 pr-2 pb-1 pl-2 t-xs fw-600" style={{ cursor: "pointer" }}
            >
              Try again
            </button>
          )}
        </div>
      )}
    </div>
  );
}
