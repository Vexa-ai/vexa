"use client";

import { Video, Search, FileText, Users, Calendar, Inbox, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type EmptyType = "meetings" | "search" | "transcripts" | "participants" | "calendar" | "generic";

interface EmptyStateProps {
  title?: string;
  message?: string;
  type?: EmptyType;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

const emptyConfig: Record<EmptyType, { icon: typeof Video; defaultTitle: string; defaultMessage: string }> = {
  meetings: {
    icon: Video,
    defaultTitle: "No meetings yet",
    defaultMessage: "Start your first transcription to see it here.",
  },
  search: {
    icon: Search,
    defaultTitle: "No results found",
    defaultMessage: "Try adjusting your search or filters.",
  },
  transcripts: {
    icon: FileText,
    defaultTitle: "No transcripts",
    defaultMessage: "Transcripts will appear here once the meeting starts.",
  },
  participants: {
    icon: Users,
    defaultTitle: "No participants",
    defaultMessage: "Participants will appear once they join.",
  },
  calendar: {
    icon: Calendar,
    defaultTitle: "No upcoming meetings",
    defaultMessage: "Your scheduled meetings will appear here.",
  },
  generic: {
    icon: Inbox,
    defaultTitle: "Nothing here",
    defaultMessage: "There's nothing to display at the moment.",
  },
};

export function EmptyState({
  title,
  message,
  type = "generic",
  action,
  className,
}: EmptyStateProps) {
  const config = emptyConfig[type];
  const Icon = config.icon;

  return (
    <div className={cn(
      "relative overflow-hidden rounded-2xl border border-dashed border-muted-foreground/25 bg-gradient-to-br from-muted/30 to-muted/10",
      className
    )}>
      <div className="absolute inset-0 bg-grid-pattern opacity-[0.02]" />
      <div className="relative flex flex-col items-center justify-center py-12 px-4">
        {/* Icon with animation */}
        <div className="relative mb-6">
          <div className="absolute inset-0 animate-pulse">
            <div className="w-20 h-20 rounded-full bg-primary/10 blur-xl" />
          </div>
          <div className="relative w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center border border-primary/20 shadow-lg shadow-primary/10">
            <Icon className="h-8 w-8 text-primary/60" />
          </div>
          {action && (
            <div className="absolute -bottom-1.5 -right-1.5 w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-primary/80 flex items-center justify-center shadow-lg shadow-primary/25">
              <Plus className="h-4 w-4 text-primary-foreground" />
            </div>
          )}
        </div>

        {/* Title */}
        <h3 className="text-lg font-semibold mb-2 text-foreground/90">
          {title || config.defaultTitle}
        </h3>

        {/* Message */}
        <p className="text-muted-foreground text-center max-w-sm mb-2 text-sm leading-relaxed">
          {message || config.defaultMessage}
        </p>

        {/* Action button */}
        {action && (
          <Button
            onClick={action.onClick}
            className="mt-4 shadow-lg shadow-primary/20 hover:shadow-primary/30 transition-all"
          >
            {action.label}
          </Button>
        )}

        {/* Decorative floating dots */}
        <div className="absolute top-6 left-8 w-2 h-2 rounded-full bg-primary/20 animate-float" />
        <div className="absolute top-12 right-10 w-2.5 h-2.5 rounded-full bg-primary/15 animate-float-delayed" />
        <div className="absolute bottom-10 left-12 w-2 h-2 rounded-full bg-primary/20 animate-float" />
      </div>
    </div>
  );
}
