"use client";

import { useEffect, useState, useMemo, useRef, useCallback } from "react";
import { RefreshCw, Video, Loader2, Search, Monitor } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { format, formatDistanceToNow } from "date-fns";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorState } from "@/components/ui/error-state";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { Platform, MeetingStatus, Meeting } from "@/types/vexa";
import { getDetailedStatus } from "@/types/vexa";
import { Input } from "@/components/ui/input";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { usePendingMeeting } from "@/hooks/use-pending-meeting";

function PlatformIcon({ platform }: { platform: string }) {
  if (platform === "google_meet") {
    return <Image src="/icons/icons8-google-meet-96.png" alt="Google Meet" width={20} height={20} className="rounded" />;
  }
  if (platform === "teams") {
    return <Image src="/icons/icons8-teams-96.png" alt="Teams" width={20} height={20} className="rounded" />;
  }
  if (platform === "browser_session") {
    return <Monitor className="h-5 w-5 text-muted-foreground" />;
  }
  return <Image src="/icons/icons8-zoom-96.png" alt="Zoom" width={20} height={20} className="rounded" />;
}

function StatusDot({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-block w-2 h-2 rounded-full",
        status === "completed" && "bg-emerald-400",
        status === "active" && "bg-emerald-400 animate-pulse",
        status === "joining" && "bg-blue-400",
        status === "awaiting_admission" && "bg-amber-400",
        status === "stopping" && "bg-amber-400",
        status === "failed" && "bg-red-400",
        status === "requested" && "bg-blue-400"
      )}
    />
  );
}

function formatDuration(startTime: string | null, endTime: string | null): string {
  if (!startTime || !endTime) return "—";
  const minutes = Math.round(
    (new Date(endTime).getTime() - new Date(startTime).getTime()) / 60000
  );
  if (minutes < 1) return "<1m";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

// v0.10.5.3 Pack D-1 (#265): parseUTCTimestamp interprets the unsuffixed-ISO
// API timestamp as UTC. date-fns format() then renders in browser-local tz.
// Pre-fix: new Date(dateStr) with unsuffixed-ISO was treated as local time,
// producing a tz-shifted display.
function formatDate(dateStr: string): string {
  const d = parseUTCTimestamp(dateStr);
  return format(d, "MMM d, HH:mm");
}

export default function MeetingsPage() {
  usePendingMeeting();
  const router = useRouter();
  const { meetings, isLoadingMeetings, isLoadingMore, hasMore, fetchMeetings, fetchMoreMeetings, error } = useMeetingsStore();

  const [searchQuery, setSearchQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<Platform | "all">("all");
  const [statusFilter, setStatusFilter] = useState<MeetingStatus | "all">("all");

  // Debounced server-side search
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const filtersRef = useRef({ search: "", status: "" as string, platform: "" as string });

  const applyFilters = useCallback((search: string, status: string, platform: string) => {
    filtersRef.current = { search, status, platform };
    fetchMeetings({
      search: search || undefined,
      status: status === "all" ? undefined : status,
      platform: platform === "all" ? undefined : platform,
    });
  }, [fetchMeetings]);

  // Initial load
  useEffect(() => {
    fetchMeetings();
  }, [fetchMeetings]);

  // Re-fetch when dropdown filters change
  useEffect(() => {
    applyFilters(searchQuery, statusFilter, platformFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, platformFilter]);

  // Debounce search input (300ms)
  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      applyFilters(value, statusFilter, platformFilter);
    }, 300);
  }, [applyFilters, statusFilter, platformFilter]);

  const filteredMeetings = meetings;

  // Infinite scroll
  const sentinelRef = useRef<HTMLDivElement>(null);
  const handleLoadMore = useCallback(() => {
    if (hasMore && !isLoadingMore && !isLoadingMeetings) {
      fetchMoreMeetings();
    }
  }, [hasMore, isLoadingMore, isLoadingMeetings, fetchMoreMeetings]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) handleLoadMore(); },
      { rootMargin: "200px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [handleLoadMore]);

  const handleRefresh = () => applyFilters(searchQuery, statusFilter, platformFilter);

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="sticky top-0 z-10 bg-background -mx-4 md:-mx-6 px-4 md:px-6 py-4 -mt-4 md:-mt-6 border-b border-border/50 space-y-4">
        {/* Top row: title + join button */}
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Meetings</h1>
            <p className="text-sm text-muted-foreground">
              Your meetings, transcribed and analyzed
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Button variant="outline" size="icon" onClick={handleRefresh} disabled={isLoadingMeetings}>
              <RefreshCw className={`h-4 w-4 ${isLoadingMeetings ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>
        {/* Filters row */}
        <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
          <div className="relative flex-1 min-w-0 sm:min-w-[180px] sm:max-w-[240px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search meetings..."
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="w-full pl-8"
            />
          </div>
          <div className="flex gap-2 min-w-0">
            <Select value={platformFilter} onValueChange={(v) => setPlatformFilter(v as Platform | "all")}>
              <SelectTrigger className="flex-1 min-w-0 sm:w-[140px] lg:w-[150px]">
                <SelectValue placeholder="All Platforms" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Platforms</SelectItem>
                <SelectItem value="google_meet">Google Meet</SelectItem>
                <SelectItem value="teams">Teams</SelectItem>
                <SelectItem value="zoom">Zoom</SelectItem>
                <SelectItem value="browser_session">Browser</SelectItem>
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as MeetingStatus | "all")}>
              <SelectTrigger className="flex-1 min-w-0 sm:w-[130px] lg:w-[150px]">
                <SelectValue placeholder="All Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Status</SelectItem>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
                <SelectItem value="joining">Joining</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* Meetings grid — Grain-style cards */}
      {error ? (
        <ErrorState error={error} onRetry={fetchMeetings} />
      ) : (
        <div className="space-y-2">
          {isLoadingMeetings ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : filteredMeetings.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <Video className="h-8 w-8 text-muted-foreground/50 mb-2" />
              <p className="text-sm text-muted-foreground">
                {searchQuery.trim() || platformFilter !== "all" || statusFilter !== "all"
                  ? "No meetings match your filters"
                  : "No meetings yet"}
              </p>
            </div>
          ) : (
            filteredMeetings.map((meeting) => (
              <MeetingCard key={meeting.id} meeting={meeting} />
            ))
          )}
          {(hasMore || isLoadingMore) && (
            <div ref={sentinelRef} className="flex justify-center py-4">
              {isLoadingMore && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
            </div>
          )}
        </div>
      )}

    </div>
  );
}

function MeetingCard({ meeting }: { meeting: Meeting }) {
  const router = useRouter();
  const statusConfig = getDetailedStatus(meeting.status, meeting.data);
  const displayTitle = meeting.data?.name || meeting.data?.title || meeting.platform_specific_id;
  const participants = meeting.data?.participants || [];

  return (
    <Card
      className="cursor-pointer transition-colors hover:bg-muted/40 border border-border rounded-lg py-2.5"
      onClick={() => router.push(`/meetings/${meeting.id}`)}
    >
      <div className="flex items-center gap-4 px-4">
        {/* Platform icon */}
        <div className="flex-shrink-0 w-8 h-8">
          <PlatformIcon platform={meeting.platform} />
        </div>

        {/* Title + ID */}
        <div className="min-w-0 flex-1">
          <span className="font-medium text-sm truncate block">{displayTitle}</span>
          {(meeting.data?.name || meeting.data?.title) && (
            <span className="block text-xs text-muted-foreground font-mono truncate">
              {meeting.platform_specific_id}
            </span>
          )}
        </div>

        {/* Status */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <StatusDot status={meeting.status} />
          <span
            className={cn(
              "text-xs hidden sm:inline",
              (meeting.status === "completed") && "text-emerald-400",
              (meeting.status === "active" || meeting.status === "joining") && "text-emerald-400",
              (meeting.status === "awaiting_admission" || meeting.status === "stopping") && "text-amber-400",
              meeting.status === "failed" && "text-red-400"
            )}
          >
            {statusConfig?.label}
          </span>
        </div>

        {/* Duration */}
        <div className="text-muted-foreground text-xs w-12 text-right flex-shrink-0">
          {formatDuration(meeting.start_time, meeting.end_time)}
        </div>

        {/* Participants (hidden on small screens) */}
        <div className="hidden lg:block text-muted-foreground text-xs w-32 flex-shrink-0 truncate">
          {participants.length > 0 ? (
            <span>
              {participants.slice(0, 2).join(", ")}
              {participants.length > 2 && ` +${participants.length - 2}`}
            </span>
          ) : (
            "—"
          )}
        </div>

        {/* Time */}
        <div className="hidden sm:block text-muted-foreground text-xs w-36 text-right flex-shrink-0 whitespace-nowrap">
          {meeting.start_time ? (
            <>
              {formatDate(meeting.start_time)}
              <span className="block text-[10px] text-muted-foreground/70">
                {formatDistanceToNow(parseUTCTimestamp(meeting.start_time), { addSuffix: true })}
              </span>
            </>
          ) : meeting.created_at ? (
            formatDate(meeting.created_at)
          ) : (
            "—"
          )}
        </div>
      </div>
    </Card>
  );
}
