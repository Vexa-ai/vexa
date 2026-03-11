"use client";

import { useEffect, useState, useCallback } from "react";
import { Loader2, Check, Clock, DoorOpen, Radio, XCircle, AlertTriangle, RefreshCw, StopCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { vexaAPI } from "@/lib/api";
import { toast } from "sonner";
import type { MeetingStatus, Platform } from "@/types/vexa";
import { DocsLink } from "@/components/docs/docs-link";

// Timeout in seconds before showing a warning
const REQUESTED_TIMEOUT_SECONDS = 30;
const JOINING_TIMEOUT_SECONDS = 60; // Give more time for joining state
// Removed BOT_CHECK_INTERVAL_MS - no longer polling, using WebSocket for real-time updates

interface BotStatusIndicatorProps {
  status: MeetingStatus;
  platform: string;
  meetingId: string;
  createdAt?: string;
  updatedAt?: string;
  errorMessage?: string;
  onRetry?: () => void;
  onStopped?: () => void;
}

const STATUS_STEPS = [
  { key: "requested", label: "Requested", description: "Bot is starting up" },
  { key: "joining", label: "Joining", description: "Connecting to meeting" },
  { key: "awaiting_admission", label: "Waiting", description: "Waiting to be admitted" },
  { key: "active", label: "Recording", description: "Transcribing audio" },
] as const;

const STATUS_ORDER: Record<string, number> = {
  requested: 0,
  joining: 1,
  awaiting_admission: 2,
  active: 3,
  completed: 4,
  failed: -1,
};

export function BotStatusIndicator({ status, platform, meetingId, createdAt, updatedAt, errorMessage, onRetry, onStopped }: BotStatusIndicatorProps) {
  const [dots, setDots] = useState("");
  const [isTimedOut, setIsTimedOut] = useState(false);
  const [isBotRunning, setIsBotRunning] = useState<boolean | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [isStopping, setIsStopping] = useState(false);

  const handleStopBot = useCallback(async () => {
    setIsStopping(true);
    try {
      await vexaAPI.stopBot(platform as Platform, meetingId);
      toast.success("Bot stopped", {
        description: "The bot has been stopped and resources freed.",
      });
      onStopped?.();
    } catch (error) {
      toast.error("Failed to stop bot", {
        description: (error as Error).message,
      });
    } finally {
      setIsStopping(false);
    }
  }, [platform, meetingId, onStopped]);

  // Animate dots for loading states
  useEffect(() => {
    if (status === "requested" || status === "joining" || status === "awaiting_admission") {
      const interval = setInterval(() => {
        setDots((prev) => (prev.length >= 3 ? "" : prev + "."));
      }, 500);
      return () => clearInterval(interval);
    }
  }, [status]);

  // Track elapsed time and check for timeout (for both requested and joining states)
  useEffect(() => {
    const isEarlyState = status === "requested" || status === "joining";
    if (!isEarlyState || !createdAt) {
      setIsTimedOut(false);
      setElapsedSeconds(0);
      return;
    }

    const timeoutSeconds = status === "requested" ? REQUESTED_TIMEOUT_SECONDS : JOINING_TIMEOUT_SECONDS;
    // For joining status, use updatedAt (when it transitioned to joining) if available
    const referenceTime = status === "joining" && updatedAt ? updatedAt : createdAt;

    const checkTimeout = () => {
      // Parse timestamp as UTC (API returns timestamps without timezone suffix)
      const reference = parseUTCTimestamp(referenceTime).getTime();
      const now = Date.now();
      const elapsed = Math.floor((now - reference) / 1000);
      setElapsedSeconds(elapsed);

      if (elapsed >= timeoutSeconds) {
        setIsTimedOut(true);
      }
    };

    checkTimeout();
    const interval = setInterval(checkTimeout, 1000);
    return () => clearInterval(interval);
  }, [status, createdAt, updatedAt]);

  // Removed polling - WebSocket now handles status updates in real-time
  // No need to check bot status via REST API when WebSocket provides live updates

  const currentStep = STATUS_ORDER[status] ?? -1;
  const isEarlyState = currentStep >= 0 && currentStep < 3;

  if (!isEarlyState) return null;

  // Show timeout warning if bot has been stuck too long and is not running
  const isStuckAndNotRunning = isTimedOut && (status === "requested" || status === "joining") && isBotRunning === false;

  if (isStuckAndNotRunning) {
    const isJoiningStuck = status === "joining";
    return (
      <Card className="border-orange-500/50 bg-orange-500/5">
        <CardContent className="pt-8 pb-8">
          <div className="flex flex-col items-center text-center">
            <div className="h-16 w-16 rounded-full bg-orange-500/10 flex items-center justify-center mb-4">
              <AlertTriangle className="h-8 w-8 text-orange-500" />
            </div>
            <h2 className="text-xl font-semibold mb-2 text-orange-600 dark:text-orange-400">
              {isJoiningStuck ? "Bot Connection Lost" : "Bot Failed to Start"}
            </h2>
            <p className="text-sm text-muted-foreground max-w-sm mb-2">
              {isJoiningStuck
                ? `The bot has been trying to join for ${elapsedSeconds} seconds but the connection was lost.`
                : `The bot has been waiting for ${elapsedSeconds} seconds but the container never started.`
              }
            </p>
            <p className="text-xs text-muted-foreground max-w-sm mb-4">
              {isJoiningStuck
                ? "This can happen due to Google's security checks or meeting access issues. Stop this bot to free up your slot and try again."
                : "This may be due to server issues or resource limits. Stop this bot to free up your slot and try again."
              }
            </p>
            <div className="flex gap-2">
              <Button
                variant="destructive"
                size="sm"
                onClick={handleStopBot}
                disabled={isStopping}
                className="gap-2"
              >
                {isStopping ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <StopCircle className="h-4 w-4" />
                )}
                Stop Bot
              </Button>
              {onRetry && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onRetry}
                  disabled={isStopping}
                  className="gap-2"
                >
                  <RefreshCw className="h-4 w-4" />
                  Retry
                </Button>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  const getStatusIcon = (stepStatus: string, isActive: boolean, isCompleted: boolean) => {
    if (isCompleted) {
      return <Check className="h-5 w-5 text-green-500" />;
    }
    if (!isActive) {
      // Small dot for inactive steps - the parent container already has bg-muted which is opaque
      return <div className="h-2.5 w-2.5 rounded-full bg-muted-foreground/40" />;
    }
    switch (stepStatus) {
      case "requested":
        return <Loader2 className="h-5 w-5 animate-spin text-primary" />;
      case "joining":
        return <DoorOpen className="h-5 w-5 text-primary animate-pulse" />;
      case "awaiting_admission":
        return <Clock className="h-5 w-5 text-orange-500 animate-pulse" />;
      default:
        return <Radio className="h-5 w-5 text-primary" />;
    }
  };

  const getStatusMessage = () => {
    switch (status) {
      case "requested":
        return "Starting transcription bot";
      case "joining":
        return "Joining the meeting";
      case "awaiting_admission":
        return "Please admit the bot to the meeting";
      default:
        return "Preparing";
    }
  };

  return (
    <Card className="border-0 shadow-lg bg-gradient-to-br from-background to-muted/30 relative">
      <CardContent className="pt-8 pb-8">
        <div className="absolute top-4 right-4">
          <DocsLink href="/docs/cookbook/track-meeting-status" />
        </div>
        {/* Main status display */}
        <div className="flex flex-col items-center text-center mb-10">
          <div className="relative mb-6">
            {/* Outer glow ring */}
            <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping" style={{ animationDuration: "2s" }} />
            {/* Inner container */}
            <div className="relative h-20 w-20 rounded-full bg-gradient-to-br from-primary/10 to-primary/5 border border-primary/20 flex items-center justify-center">
              {status === "awaiting_admission" ? (
                <Clock className="h-10 w-10 text-orange-500" />
              ) : (
                <Loader2 className="h-10 w-10 animate-spin text-primary" />
              )}
            </div>
          </div>

          <h2 className="text-xl font-semibold mb-2">
            {getStatusMessage()}
            <span className="inline-block w-6 text-left">{dots}</span>
          </h2>

          <p className="text-sm text-muted-foreground max-w-sm">
            {status === "awaiting_admission" ? (
              <>Look for <span className="font-medium text-foreground">Vexa Bot</span> in your meeting&apos;s waiting room and click admit</>
            ) : (
              "This usually takes a few seconds"
            )}
          </p>
        </div>

        {/* Progress steps */}
        <div className="max-w-md mx-auto">
          <div className="relative">
            {/* Progress line */}
            <div className="absolute left-[22px] top-0 bottom-0 w-0.5 bg-muted" />
            <div
              className="absolute left-[22px] top-0 w-0.5 bg-primary transition-all duration-500"
              style={{ height: `${Math.max(0, currentStep) * 33.33}%` }}
            />

            {/* Steps */}
            <div className="relative space-y-6">
              {STATUS_STEPS.map((step, index) => {
                const isCompleted = currentStep > index;
                const isActive = currentStep === index;

                return (
                  <div
                    key={step.key}
                    className={cn(
                      "flex items-start gap-4 transition-opacity duration-300",
                      !isCompleted && !isActive && "opacity-40"
                    )}
                  >
                    {/* Step icon - with solid background to hide the progress line */}
                    <div className="relative flex-shrink-0">
                      {/* Solid background layer to hide the line */}
                      <div className="absolute inset-0 h-11 w-11 rounded-full bg-background" />
                      {/* Actual step circle */}
                      <div className={cn(
                        "relative h-11 w-11 rounded-full flex items-center justify-center border-2 transition-all duration-300",
                        isCompleted && "bg-green-50 border-green-200 dark:bg-green-900 dark:border-green-800",
                        isActive && "bg-background border-primary shadow-lg shadow-primary/20",
                        !isCompleted && !isActive && "bg-background border-muted-foreground/20"
                      )}>
                        {getStatusIcon(step.key, isActive, isCompleted)}
                      </div>
                    </div>

                    {/* Step text */}
                    <div className="flex-1 pt-2">
                      <p className={cn(
                        "font-medium text-sm",
                        isActive && "text-primary"
                      )}>
                        {step.label}
                        {isActive && <span className="inline-block w-4 text-left text-muted-foreground">{dots}</span>}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {step.description}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Stop button - always visible at the bottom */}
        <div className="mt-8 pt-6 border-t border-muted flex justify-center">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleStopBot}
            disabled={isStopping}
            className="text-muted-foreground hover:text-destructive gap-2"
          >
            {isStopping ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <StopCircle className="h-4 w-4" />
            )}
            Cancel and stop bot
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function BotFailedIndicator({
  status,
  errorMessage,
  errorCode,
  onRetry
}: {
  status: MeetingStatus;
  errorMessage?: string;
  errorCode?: string;
  onRetry?: () => void;
}) {
  if (status !== "failed") return null;

  // Determine error display
  const getErrorTitle = () => {
    if (errorCode) {
      switch (errorCode.toLowerCase()) {
        case "admission_timeout":
        case "not_admitted":
          return "Bot was not admitted";
        case "meeting_ended":
          return "Meeting has ended";
        case "kicked":
        case "removed":
          return "Bot was removed from meeting";
        case "connection_failed":
          return "Connection failed";
        default:
          return "Transcription failed";
      }
    }
    return "Transcription failed";
  };

  const getDefaultMessage = () => {
    if (errorMessage) return errorMessage;
    return "The bot was unable to join or complete the transcription. This can happen if the meeting ended or the bot was removed.";
  };

  return (
    <Card className="border-destructive/50 bg-destructive/5">
      <CardContent className="pt-8 pb-8">
        <div className="flex flex-col items-center text-center">
          <div className="h-16 w-16 rounded-full bg-destructive/10 flex items-center justify-center mb-4">
            <XCircle className="h-8 w-8 text-destructive" />
          </div>
          <h2 className="text-xl font-semibold mb-2 text-destructive">
            {getErrorTitle()}
          </h2>
          <p className="text-sm text-muted-foreground max-w-sm mb-4">
            {getDefaultMessage()}
          </p>
          {errorCode && (
            <p className="text-xs text-muted-foreground/60 font-mono mb-4">
              Error: {errorCode}
            </p>
          )}
          {onRetry && (
            <button
              onClick={onRetry}
              className="text-sm font-medium text-primary hover:underline"
            >
              Try again with a new bot
            </button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
