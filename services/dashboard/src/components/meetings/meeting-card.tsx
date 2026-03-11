"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { formatDistanceToNow, format } from "date-fns";
import { Clock, ChevronRight, Calendar, MessageSquare, FileText, Pencil, Check, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Meeting } from "@/types/vexa";
import { getDetailedStatus } from "@/types/vexa";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { useMeetingsStore } from "@/stores/meetings-store";
import { toast } from "sonner";

interface MeetingCardProps {
  meeting: Meeting;
}

// Platform icons using actual icon files from public folder
function GoogleMeetIcon({ className }: { className?: string }) {
  return (
    <Image
      src="/icons/icons8-google-meet-96.png"
      alt="Google Meet"
      width={40}
      height={40}
      className={className}
    />
  );
}

function TeamsIcon({ className }: { className?: string }) {
  return (
    <Image
      src="/icons/icons8-teams-96.png"
      alt="Microsoft Teams"
      width={40}
      height={40}
      className={className}
    />
  );
}

function ZoomIcon({ className }: { className?: string }) {
  return (
    <Image
      src="/icons/icons8-zoom-96.png"
      alt="Zoom"
      width={40}
      height={40}
      className={className}
    />
  );
}

function PlatformIcon({ platform, className }: { platform: string; className?: string }) {
  if (platform === "google_meet") return <GoogleMeetIcon className={className} />;
  if (platform === "teams") return <TeamsIcon className={className} />;
  return <ZoomIcon className={className} />;
}

export function MeetingCard({ meeting }: MeetingCardProps) {
  const statusConfig = getDetailedStatus(meeting.status, meeting.data);
  const updateMeetingData = useMeetingsStore((state) => state.updateMeetingData);
  const isGoogleMeet = meeting.platform === "google_meet";
  // Display title from API data (name or title field)
  const displayTitle = meeting.data?.name || meeting.data?.title;
  const isActive = meeting.status === "active";
  
  // Title editing state
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);

  const duration = meeting.start_time && meeting.end_time
    ? Math.round(
        (new Date(meeting.end_time).getTime() - new Date(meeting.start_time).getTime()) / 60000
      )
    : null;

  const formatDuration = (minutes: number) => {
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  };

  // Build detailed status info for tooltip
  const getStatusTooltipContent = () => {
    const lines: string[] = [];
    
    // Status description
    if (statusConfig.description) {
      lines.push(statusConfig.description);
    }
    
    // Completion reason details
    if (meeting.data?.completion_reason) {
      const reason = meeting.data.completion_reason;
      if (reason !== "stopped" && reason !== "meeting_ended") {
        const formattedReason = reason
          .split("_")
          .map(word => word.charAt(0).toUpperCase() + word.slice(1))
          .join(" ");
        lines.push(`Reason: ${formattedReason}`);
      }
    }
    
    // Status transitions summary
    if (meeting.data?.status_transition && meeting.data.status_transition.length > 0) {
      const transitions = meeting.data.status_transition;
      const lastTransition = transitions[transitions.length - 1];
      
      if (lastTransition.timestamp) {
        try {
          const timestamp = parseUTCTimestamp(lastTransition.timestamp);
          lines.push(`Last updated: ${formatDistanceToNow(timestamp, { addSuffix: true })}`);
        } catch (e) {
          // Ignore parsing errors
        }
      }
      
      // Show transition count if more than 1
      if (transitions.length > 1) {
        lines.push(`${transitions.length} status changes`);
      }
    }
    
    // Start/end times if available
    if (meeting.start_time) {
      try {
        const startTime = parseUTCTimestamp(meeting.start_time);
        lines.push(`Started: ${format(startTime, "MMM d, HH:mm")}`);
      } catch (e) {
        // Ignore parsing errors
      }
    }
    
    if (meeting.end_time) {
      try {
        const endTime = parseUTCTimestamp(meeting.end_time);
        lines.push(`Ended: ${format(endTime, "MMM d, HH:mm")}`);
      } catch (e) {
        // Ignore parsing errors
      }
    }
    
    return lines;
  };

  const tooltipContent = getStatusTooltipContent();

  // Handle title editing
  const handleStartEdit = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setEditedTitle(displayTitle || "");
    setIsEditingTitle(true);
  };

  const handleSaveTitle = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!editedTitle.trim()) {
      setIsEditingTitle(false);
      return;
    }
    setIsSavingTitle(true);
    try {
      await updateMeetingData(meeting.platform, meeting.platform_specific_id, {
        name: editedTitle.trim(),
      });
      setIsEditingTitle(false);
      toast.success("Title updated");
    } catch (error) {
      toast.error("Failed to update title");
    } finally {
      setIsSavingTitle(false);
    }
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsEditingTitle(false);
    setEditedTitle("");
  };

  const handleKeyDown = async (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && editedTitle.trim() && !isSavingTitle) {
      e.preventDefault();
      e.stopPropagation();
      setIsSavingTitle(true);
      try {
        await updateMeetingData(meeting.platform, meeting.platform_specific_id, {
          name: editedTitle.trim(),
        });
        setIsEditingTitle(false);
        toast.success("Title updated");
      } catch (error) {
        toast.error("Failed to update title");
      } finally {
        setIsSavingTitle(false);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setIsEditingTitle(false);
      setEditedTitle("");
    }
  };

  return (
    <Link href={`/meetings/${meeting.id}`} className="block group" onClick={(e) => isEditingTitle && e.preventDefault()}>
      <Card className={cn(
        "relative overflow-hidden transition-all duration-200",
        "border border-border hover:border-gray-300",
        "bg-card",
        isActive && "ring-1 ring-emerald-400/30"
      )}>
        {/* Platform color accent */}
        <div className={cn(
          "absolute top-0 left-0 w-1 h-full",
          meeting.platform === "google_meet" ? "bg-green-500" : meeting.platform === "teams" ? "bg-[#5059C9]" : "bg-blue-500"
        )} />

        <div className="py-3 px-4">
          <div className="flex items-center gap-3">
            {/* Platform Icon */}
            <div className="flex-shrink-0 relative">
              <PlatformIcon platform={meeting.platform} className="h-10 w-10 rounded-lg" />
              {/* Active indicator */}
              {isActive && (
                <div className="absolute -top-1 -right-1">
                  <span className="relative flex h-3 w-3">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500 border-2 border-white dark:border-gray-900" />
                  </span>
                </div>
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0 space-y-1">
              {/* Header row */}
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  {isEditingTitle ? (
                    <div className="flex items-center gap-2">
                      <Input
                        value={editedTitle}
                        onChange={(e) => setEditedTitle(e.target.value)}
                        className="text-base font-semibold h-8 flex-1"
                        placeholder="Meeting title..."
                        autoFocus
                        disabled={isSavingTitle}
                        onKeyDown={handleKeyDown}
                        onClick={(e) => e.stopPropagation()}
                      />
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8"
                        onClick={handleSaveTitle}
                        disabled={isSavingTitle}
                      >
                        <Check className="h-4 w-4" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8"
                        onClick={handleCancelEdit}
                        disabled={isSavingTitle}
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 group/title">
                      <h3 className="font-semibold text-base truncate text-foreground">
                        {displayTitle || meeting.platform_specific_id}
                      </h3>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8 opacity-0 group-hover/title:opacity-100 transition-opacity"
                        onClick={handleStartEdit}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                    </div>
                  )}
                  {meeting.data?.participants && meeting.data.participants.length > 0 ? (
                    <p className="text-xs text-muted-foreground truncate mt-0.5">
                      With {meeting.data.participants.slice(0, 3).join(", ")}
                      {meeting.data.participants.length > 3 && ` +${meeting.data.participants.length - 3}`}
                    </p>
                  ) : displayTitle && !isEditingTitle && (
                    <p className="text-xs text-muted-foreground font-mono truncate mt-0.5">
                      {meeting.platform_specific_id}
                    </p>
                  )}
                </div>

                {/* Status badge with tooltip */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div>
                      <Badge
                        variant="secondary"
                        className={cn(
                          "flex-shrink-0 text-xs font-medium px-2 py-0.5 cursor-help",
                          statusConfig.bgColor,
                          statusConfig.color,
                          isActive && "animate-pulse"
                        )}
                      >
                        {statusConfig.label}
                      </Badge>
                    </div>
                  </TooltipTrigger>
                  {tooltipContent.length > 0 && (
                    <TooltipContent side="top" className="max-w-xs">
                      <div className="space-y-1">
                        {tooltipContent.map((line, index) => (
                          <div key={index} className="text-xs">
                            {line}
                          </div>
                        ))}
                      </div>
                    </TooltipContent>
                  )}
                </Tooltip>
              </div>

              {/* Status description */}
              {statusConfig.description && (
                <div className="text-xs text-muted-foreground">
                  {statusConfig.description}
                </div>
              )}

              {/* Meta row */}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {meeting.start_time && (
                  <div className="flex items-center gap-1.5 text-muted-foreground">
                    <Calendar className="h-3.5 w-3.5" />
                    <span>{format(new Date(meeting.start_time), "MMM d, yyyy")}</span>
                  </div>
                )}

                {meeting.start_time && (
                  <div className="flex items-center gap-1.5 text-muted-foreground">
                    <Clock className="h-3.5 w-3.5" />
                    <span>{formatDistanceToNow(new Date(meeting.start_time), { addSuffix: true })}</span>
                  </div>
                )}

                {duration && (
                  <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-muted/50">
                    <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-muted-foreground">{formatDuration(duration)}</span>
                  </div>
                )}

                {meeting.data?.notes && meeting.data.notes.trim() && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="flex items-center gap-1.5 text-muted-foreground cursor-help">
                        <FileText className="h-3.5 w-3.5" />
                        <span>Note</span>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="max-w-xs">
                      <div className="text-xs">
                        <div className="font-medium mb-1">Note:</div>
                        <div className="text-muted-foreground">
                          {typeof meeting.data.notes === "string" && meeting.data.notes.length > 100
                            ? `${meeting.data.notes.substring(0, 100)}...`
                            : String(meeting.data.notes)}
                        </div>
                      </div>
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>

            </div>

            {/* Arrow */}
            <div className="flex-shrink-0 self-center p-1.5 rounded-full transition-colors group-hover:bg-accent">
              <ChevronRight className="h-4 w-4 text-muted-foreground transition-colors group-hover:text-foreground" />
            </div>
          </div>
        </div>
      </Card>
    </Link>
  );
}
