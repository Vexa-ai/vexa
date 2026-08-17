"use client";

import { AlertCircle, CreditCard, Clock, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { denialActionUrl } from "@/lib/join-error";
import { getWebappUrl } from "@/lib/docs/webapp-url";
import type {
  ServiceDenialKind,
  ServiceDenialPresentation,
} from "@/lib/service-denial";

/**
 * The in-flow rendering of a refused Join.
 *
 * A panel, not a toast: a paywall is a thing the customer has to act on, and a
 * toast that vanishes on a timer loses both the reason and the fix. Styling
 * follows the modal's existing inline-notice idiom (tinted border + wash).
 */

const ACCENT: Record<ServiceDenialKind, string> = {
  paywall: "border-amber-500/40 bg-amber-500/5",
  setup: "border-amber-500/40 bg-amber-500/5",
  limit: "border-blue-500/40 bg-blue-500/5",
  retryable: "border-muted-foreground/30 bg-muted/40",
  unknown: "border-destructive/40 bg-destructive/5",
};

const TITLE_TONE: Record<ServiceDenialKind, string> = {
  paywall: "text-amber-700 dark:text-amber-300",
  setup: "text-amber-700 dark:text-amber-300",
  limit: "text-blue-700 dark:text-blue-300",
  retryable: "text-foreground",
  unknown: "text-destructive",
};

function DenialIcon({ kind }: { kind: ServiceDenialKind }) {
  const className = "h-4 w-4 shrink-0";
  if (kind === "paywall") return <CreditCard className={className} />;
  if (kind === "setup") return <Settings2 className={className} />;
  if (kind === "retryable") return <Clock className={className} />;
  return <AlertCircle className={className} />;
}

export function ServiceDenialPanel({
  presentation,
  onRetry,
}: {
  presentation: ServiceDenialPresentation;
  onRetry?: () => void;
}) {
  const { kind, title, body, action, retryable, reason } = presentation;

  return (
    <div
      role="alert"
      data-testid="service-denial-panel"
      data-denial-kind={kind}
      data-denial-reason={reason}
      className={cn(
        "space-y-2 rounded-lg border p-3 animate-fade-in",
        ACCENT[kind],
      )}
    >
      <p
        className={cn(
          "flex items-center gap-2 text-sm font-semibold",
          TITLE_TONE[kind],
        )}
      >
        <DenialIcon kind={kind} />
        {title}
      </p>
      <p className="text-xs text-muted-foreground">{body}</p>
      {(action || (retryable && onRetry)) && (
        <div className="flex items-center gap-2 pt-1">
          {action && (
            <Button
              type="button"
              size="sm"
              onClick={() =>
                window.open(
                  denialActionUrl(getWebappUrl(), action.href),
                  "_blank",
                  "noopener,noreferrer",
                )
              }
            >
              {action.label}
            </Button>
          )}
          {retryable && onRetry && (
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              Try again
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
