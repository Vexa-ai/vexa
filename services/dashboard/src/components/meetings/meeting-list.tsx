"use client";

import { Video, Plus, Sparkles } from "lucide-react";
import { MeetingCard } from "./meeting-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { useJoinModalStore } from "@/stores/join-modal-store";
import type { Meeting } from "@/types/vexa";
import { cn } from "@/lib/utils";

interface MeetingListProps {
  meetings: Meeting[];
  isLoading?: boolean;
  limit?: number;
  emptyMessage?: string;
  showJoinCTA?: boolean;
}

export function MeetingList({
  meetings,
  isLoading,
  limit,
  emptyMessage = "No meetings found",
  showJoinCTA = true,
}: MeetingListProps) {
  const openJoinModal = useJoinModalStore((state) => state.openModal);

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(limit || 3)].map((_, i) => (
          <MeetingCardSkeleton key={i} index={i} />
        ))}
      </div>
    );
  }

  const displayedMeetings = limit ? meetings.slice(0, limit) : meetings;

  if (displayedMeetings.length === 0) {
    return (
      <div className="relative overflow-hidden rounded-2xl border border-dashed border-muted-foreground/25 bg-gradient-to-br from-muted/30 to-muted/10">
        <div className="absolute inset-0 bg-grid-pattern opacity-[0.02]" />
        <div className="relative flex flex-col items-center justify-center py-16 px-4">
          {/* Animated illustration */}
          <div className="relative mb-8">
            <div className="absolute inset-0 animate-pulse">
              <div className="w-24 h-24 rounded-full bg-primary/10 blur-xl" />
            </div>
            <div className="relative w-20 h-20 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center border border-primary/20 shadow-lg shadow-primary/10">
              <Video className="h-10 w-10 text-primary/60" />
            </div>
            <div className="absolute -bottom-2 -right-2 w-10 h-10 rounded-xl bg-gradient-to-br from-green-500 to-green-600 flex items-center justify-center shadow-lg shadow-green-500/25 animate-bounce-slow">
              <Plus className="h-5 w-5 text-white" />
            </div>
          </div>

          {/* Message */}
          <h3 className="text-xl font-semibold mb-2 text-foreground/90">No meetings yet</h3>
          <p className="text-muted-foreground text-center max-w-sm mb-8 leading-relaxed">
            {emptyMessage}
          </p>

          {/* CTA */}
          {showJoinCTA && (
            <Button
              onClick={openJoinModal}
              size="lg"
              className="shadow-lg shadow-primary/25 hover:shadow-primary/40 transition-all duration-300 hover:scale-105"
            >
              <Sparkles className="h-4 w-4 mr-2" />
              Start Your First Transcription
            </Button>
          )}

          {/* Decorative elements */}
          <div className="absolute top-8 left-8 w-2 h-2 rounded-full bg-primary/20 animate-float" />
          <div className="absolute top-16 right-12 w-3 h-3 rounded-full bg-green-500/20 animate-float-delayed" />
          <div className="absolute bottom-12 left-16 w-2.5 h-2.5 rounded-full bg-blue-500/20 animate-float" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {displayedMeetings.map((meeting, index) => (
        <div
          key={`${meeting.id}-${meeting.platform_specific_id}-${index}`}
          className="animate-fade-in-up"
          style={{ animationDelay: `${index * 50}ms`, animationFillMode: "backwards" }}
        >
          <MeetingCard meeting={meeting} />
        </div>
      ))}
    </div>
  );
}

function MeetingCardSkeleton({ index = 0 }: { index?: number }) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border-0 bg-card p-5 pl-6 shadow-sm animate-fade-in",
      )}
      style={{ animationDelay: `${index * 100}ms`, animationFillMode: "backwards" }}
    >
      {/* Platform accent skeleton */}
      <div className="absolute top-0 left-0 w-1 h-full bg-muted animate-pulse" />

      <div className="flex items-start gap-4">
        {/* Icon skeleton */}
        <Skeleton className="h-12 w-12 rounded-xl flex-shrink-0" />

        <div className="flex-1 space-y-3">
          {/* Title and status */}
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1.5">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="h-6 w-20 rounded-full" />
          </div>

          {/* Meta info */}
          <div className="flex items-center gap-4">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>

          {/* Participants */}
          <div className="flex items-center gap-2">
            <div className="flex -space-x-2">
              <Skeleton className="h-6 w-6 rounded-full" />
              <Skeleton className="h-6 w-6 rounded-full" />
              <Skeleton className="h-6 w-6 rounded-full" />
            </div>
            <Skeleton className="h-3 w-32" />
          </div>
        </div>

        {/* Arrow skeleton */}
        <Skeleton className="h-9 w-9 rounded-full flex-shrink-0" />
      </div>
    </div>
  );
}
