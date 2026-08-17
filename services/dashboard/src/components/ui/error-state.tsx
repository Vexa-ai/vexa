"use client";

import { AlertCircle, RefreshCw, WifiOff, ServerCrash, FileQuestion, CreditCard } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ErrorType = "connection" | "server" | "not-found" | "generic" | "subscription";

interface ErrorStateProps {
  title?: string;
  message?: string;
  error?: string;
  type?: ErrorType;
  onRetry?: () => void;
  onAction?: () => void;
  actionLabel?: string;
  className?: string;
}

const errorConfig: Record<ErrorType, { icon: typeof AlertCircle; defaultTitle: string; defaultMessage: string }> = {
  connection: {
    icon: WifiOff,
    defaultTitle: "Connection Error",
    defaultMessage: "Unable to connect to the server. Please check your internet connection and try again.",
  },
  server: {
    icon: ServerCrash,
    defaultTitle: "Server Unavailable",
    defaultMessage: "The server is temporarily unavailable. This might be due to maintenance or high traffic.",
  },
  "not-found": {
    icon: FileQuestion,
    defaultTitle: "Not Found",
    defaultMessage: "The requested resource could not be found.",
  },
  generic: {
    icon: AlertCircle,
    defaultTitle: "Something went wrong",
    defaultMessage: "An unexpected error occurred. Please try again.",
  },
  subscription: {
    icon: CreditCard,
    defaultTitle: "Subscription Required",
    defaultMessage: "A subscription is required to access this feature.",
  },
};

function getErrorType(error?: string): ErrorType {
  if (!error) return "generic";
  const lowerError = error.toLowerCase();
  if (lowerError.includes("502") || lowerError.includes("503") || lowerError.includes("504")) {
    return "server";
  }
  if (lowerError.includes("network") || lowerError.includes("fetch") || lowerError.includes("connection")) {
    return "connection";
  }
  if (lowerError.includes("404") || lowerError.includes("not found")) {
    return "not-found";
  }
  return "generic";
}

export function ErrorState({
  title,
  message,
  error,
  type,
  onRetry,
  onAction,
  actionLabel,
  className,
}: ErrorStateProps) {
  const errorType = type || getErrorType(error);
  const config = errorConfig[errorType];
  const Icon = config.icon;

  return (
    <div className={cn(
      "relative overflow-hidden rounded-2xl border border-destructive/20 bg-gradient-to-br from-destructive/5 to-destructive/10",
      className
    )}>
      <div className="absolute inset-0 bg-grid-pattern opacity-[0.02]" />
      <div className="relative flex flex-col items-center justify-center py-12 px-4">
        {/* Icon */}
        <div className="relative mb-6">
          <div className="absolute inset-0 animate-pulse">
            <div className="w-20 h-20 rounded-full bg-destructive/20 blur-xl" />
          </div>
          <div className="relative w-16 h-16 rounded-2xl bg-gradient-to-br from-destructive/20 to-destructive/10 flex items-center justify-center border border-destructive/20">
            <Icon className="h-8 w-8 text-destructive/70" />
          </div>
        </div>

        {/* Title */}
        <h3 className="text-lg font-semibold mb-2 text-foreground/90">
          {title || config.defaultTitle}
        </h3>

        {/* Message */}
        <p className="text-muted-foreground text-center max-w-md mb-2 text-sm leading-relaxed">
          {message || config.defaultMessage}
        </p>

        {/* Technical error (if different from message) */}
        {error && error !== message && (
          <p className="text-xs text-muted-foreground/60 font-mono bg-muted/50 px-3 py-1.5 rounded-md mb-6 max-w-md truncate">
            {error}
          </p>
        )}

        {/* Action button */}
        {onAction && actionLabel && (
          <Button
            onClick={onAction}
            variant="outline"
            className="mt-4 border-destructive/30 hover:bg-destructive/10 hover:border-destructive/50 transition-all"
          >
            {actionLabel}
          </Button>
        )}

        {/* Retry button */}
        {onRetry && (
          <Button
            onClick={onRetry}
            variant="outline"
            className="mt-4 border-destructive/30 hover:bg-destructive/10 hover:border-destructive/50 transition-all"
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Try Again
          </Button>
        )}
      </div>
    </div>
  );
}

// Compact version for inline use
export function ErrorMessage({
  message,
  onRetry,
  className,
}: {
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div className={cn(
      "flex items-center gap-3 p-4 rounded-lg bg-destructive/10 border border-destructive/20",
      className
    )}>
      <AlertCircle className="h-5 w-5 text-destructive flex-shrink-0" />
      <p className="text-sm text-destructive flex-1">{message}</p>
      {onRetry && (
        <Button
          onClick={onRetry}
          variant="ghost"
          size="sm"
          className="text-destructive hover:text-destructive hover:bg-destructive/10"
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
