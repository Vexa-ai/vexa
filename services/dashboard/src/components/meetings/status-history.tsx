"use client";

import { useState } from "react";
import { formatDistanceToNow, format } from "date-fns";
import { ChevronDown, Clock, User, Bot, Zap, CheckCircle2, XCircle, Radio, DoorOpen } from "lucide-react";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { StatusTransition, MeetingStatus } from "@/types/vexa";
import { DocsLink } from "@/components/docs/docs-link";

interface StatusHistoryProps {
  transitions?: StatusTransition[];
  className?: string;
}

// Status configuration with icons and colors for light/dark mode
const STATUS_CONFIG: Record<string, {
  icon: React.ReactNode;
  color: string;
  bgColor: string;
  dotColor: string;
}> = {
  requested: {
    icon: <Zap className="h-3 w-3" />,
    color: "text-gray-600 dark:text-gray-300",
    bgColor: "bg-gray-100 dark:bg-gray-800",
    dotColor: "bg-gray-400 dark:bg-gray-500",
  },
  joining: {
    icon: <DoorOpen className="h-3 w-3" />,
    color: "text-yellow-600 dark:text-yellow-400",
    bgColor: "bg-yellow-100 dark:bg-yellow-950/50",
    dotColor: "bg-yellow-500 dark:bg-yellow-400",
  },
  awaiting_admission: {
    icon: <Clock className="h-3 w-3" />,
    color: "text-amber-600 dark:text-amber-400",
    bgColor: "bg-amber-100 dark:bg-amber-950/50",
    dotColor: "bg-amber-500 dark:bg-amber-400",
  },
  active: {
    icon: <Radio className="h-3 w-3" />,
    color: "text-green-600 dark:text-green-400",
    bgColor: "bg-green-100 dark:bg-green-950/50",
    dotColor: "bg-green-500 dark:bg-green-400",
  },
  completed: {
    icon: <CheckCircle2 className="h-3 w-3" />,
    color: "text-blue-600 dark:text-blue-400",
    bgColor: "bg-blue-100 dark:bg-blue-950/50",
    dotColor: "bg-blue-500 dark:bg-blue-400",
  },
  failed: {
    icon: <XCircle className="h-3 w-3" />,
    color: "text-red-600 dark:text-red-400",
    bgColor: "bg-red-100 dark:bg-red-950/50",
    dotColor: "bg-red-500 dark:bg-red-400",
  },
};

// Get status config with fallback
const getStatusConfig = (status: string) => {
  return STATUS_CONFIG[status] || {
    icon: <div className="h-2 w-2 rounded-full bg-current" />,
    color: "text-gray-600 dark:text-gray-400",
    bgColor: "bg-gray-100 dark:bg-gray-800",
    dotColor: "bg-gray-400",
  };
};

// Get label for status
const getStatusLabel = (status: string): string => {
  const labels: Record<string, string> = {
    requested: "Requested",
    joining: "Joining",
    awaiting_admission: "Waiting",
    active: "Active",
    completed: "Completed",
    failed: "Failed",
  };
  return labels[status] || status;
};

// Get source icon
const getSourceIcon = (source?: string) => {
  switch (source) {
    case "user":
      return <User className="h-3 w-3" />;
    case "bot_callback":
      return <Bot className="h-3 w-3" />;
    default:
      return null;
  }
};

export function StatusHistory({ transitions, className }: StatusHistoryProps) {
  const [isOpen, setIsOpen] = useState(false);

  if (!transitions || transitions.length === 0) {
    return null;
  }

  // Sort transitions by timestamp (oldest first for display, newest at bottom)
  const sortedTransitions = [...transitions].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen} className={className}>
      <CollapsibleTrigger className="w-full">
        <div className="flex items-center justify-between py-2 px-3 -mx-3 rounded-lg hover:bg-muted/50 transition-colors group cursor-pointer">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Clock className="h-4 w-4 text-muted-foreground" />
            <span>Status History</span>
            <span className="text-xs text-muted-foreground font-normal px-1.5 py-0.5 rounded-full bg-muted">
              {transitions.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <DocsLink href="/docs/cookbook/get-status-history" />
            <ChevronDown
            className={cn(
              "h-4 w-4 text-muted-foreground transition-transform duration-200",
              isOpen && "rotate-180"
            )}
          />
          </div>
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="pt-3 pb-1">
          <div className="relative pl-3">
            {/* Timeline line */}
            <div className="absolute left-[5px] top-1 bottom-1 w-0.5 bg-gradient-to-b from-muted-foreground/20 via-muted-foreground/10 to-transparent" />

            {/* Timeline items */}
            <div className="space-y-4">
              {sortedTransitions.map((transition, index) => {
                const config = getStatusConfig(transition.to);
                const timestamp = parseUTCTimestamp(transition.timestamp);
                const isLast = index === sortedTransitions.length - 1;

                return (
                  <div key={index} className="relative flex items-start gap-3 group">
                    {/* Timeline dot */}
                    <div
                      className={cn(
                        "relative z-10 w-[11px] h-[11px] rounded-full ring-2 ring-background transition-all",
                        isLast ? config.dotColor : "bg-muted-foreground/30",
                        isLast && "scale-110"
                      )}
                    />

                    {/* Content */}
                    <div className="flex-1 min-w-0 -mt-0.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        {/* Status badge */}
                        <span
                          className={cn(
                            "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium transition-colors",
                            config.bgColor,
                            config.color
                          )}
                        >
                          {config.icon}
                          {getStatusLabel(transition.to)}
                        </span>

                        {/* Source indicator */}
                        {transition.source && (
                          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                            {getSourceIcon(transition.source)}
                            <span className="capitalize">{transition.source.replace("_", " ")}</span>
                          </span>
                        )}
                      </div>

                      {/* Timestamp */}
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-muted-foreground font-mono">
                          {format(timestamp, "HH:mm:ss")}
                        </span>
                        <span className="text-xs text-muted-foreground/60">
                          {formatDistanceToNow(timestamp, { addSuffix: true })}
                        </span>
                      </div>

                      {/* Reason if present */}
                      {(transition.reason || transition.completion_reason) && (
                        <p className="text-xs text-muted-foreground/80 mt-1 italic">
                          {transition.reason || transition.completion_reason}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
