"use client";

import { useEffect, useState, useMemo } from "react";
import { Plus, RefreshCw, CreditCard, Video, Loader2, Search } from "lucide-react";
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
import { useJoinModalStore } from "@/stores/join-modal-store";
import type { Platform, MeetingStatus, Meeting } from "@/types/vexa";
import { getDetailedStatus } from "@/types/vexa";
import { DocsLink } from "@/components/docs/docs-link";
import { getWebappUrl } from "@/lib/docs/webapp-url";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { usePendingMeeting } from "@/hooks/use-pending-meeting";

function PlatformIcon({ platform }: { platform: string }) {
  if (platform === "google_meet") {
    return <Image src="/icons/icons8-google-meet-96.png" alt="Google Meet" width={20} height={20} className="rounded" />;
  }
  if (platform === "teams") {
    return <Image src="/icons/icons8-teams-96.png" alt="Teams" width={20} height={20} className="rounded" />;
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

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return format(d, "MMM d, HH:mm");
}

export default function MeetingsPage() {
  usePendingMeeting();
  const { meetings, isLoadingMeetings, fetchMeetings, error, subscriptionRequired } = useMeetingsStore();
  const openJoinModal = useJoinModalStore((state) => state.openModal);

  const [searchQuery, setSearchQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<Platform | "all">("all");
  const [statusFilter, setStatusFilter] = useState<MeetingStatus | "all">("all");

  useEffect(() => {
    fetchMeetings();
  }, [fetchMeetings]);

  const filteredMeetings = useMemo(() => {
    return meetings.filter((meeting) => {
      if (platformFilter !== "all" && meeting.platform !== platformFilter) return false;
      if (statusFilter !== "all" && meeting.status !== statusFilter) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase();
        const name = (meeting.data?.name || meeting.data?.title || "").toLowerCase();
        const nativeId = (meeting.platform_specific_id || "").toLowerCase();
        if (!name.includes(q) && !nativeId.includes(q)) return false;
      }
      return true;
    });
  }, [meetings, platformFilter, statusFilter, searchQuery]);

  const handleRefresh = () => fetchMeetings();

  const handleSubscribe = () => {
    window.open(`${getWebappUrl()}/pricing`, "_blank");
  };

  return (
    <div className="space-y-6">
      {subscriptionRequired && (
        <div className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950 p-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <CreditCard className="h-5 w-5 text-amber-600 dark:text-amber-400 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-amber-800 dark:text-amber-200">Subscription required</p>
              <p className="text-xs text-amber-700 dark:text-amber-300">
                Subscribe to a plan to create new bots and access the API.
              </p>
            </div>
          </div>
          <Button onClick={handleSubscribe} size="sm" className="bg-amber-600 hover:bg-amber-700 text-white flex-shrink-0">
            View Plans
          </Button>
        </div>
      )}

      {/* Header */}
      <div className="sticky top-0 z-10 bg-background -mx-4 md:-mx-6 px-4 md:px-6 py-4 -mt-4 md:-mt-6 border-b border-border/50 space-y-4">
        {/* Top row: title + join button */}
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Meetings</h1>
              <DocsLink href="/docs/rest/meetings#list-meetings" />
            </div>
            <p className="text-sm text-muted-foreground">
              Browse and search your meeting transcriptions
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Button variant="outline" size="icon" onClick={handleRefresh} disabled={isLoadingMeetings}>
              <RefreshCw className={`h-4 w-4 ${isLoadingMeetings ? "animate-spin" : ""}`} />
            </Button>
            {!subscriptionRequired && (
              <div className="flex items-center">
                <Button onClick={openJoinModal}>
                  <Plus className="mr-2 h-4 w-4" />
                  <span className="hidden sm:inline">Join Meeting</span>
                  <span className="sm:hidden">Join</span>
                </Button>
                <DocsLink href="/docs/rest/bots#create-bot" />
              </div>
            )}
          </div>
        </div>
        {/* Filters row */}
        <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
          <div className="relative flex-1 min-w-0 sm:min-w-[180px] sm:max-w-[240px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search meetings..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
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

      {/* Meetings table */}
      {error ? (
        <ErrorState error={error} onRetry={fetchMeetings} />
      ) : subscriptionRequired && meetings.length === 0 ? (
        <ErrorState
          type="subscription"
          title="Subscribe to continue"
          message="Your trial has ended. Subscribe to a plan to create bots and access meeting transcriptions."
          actionLabel="View Plans"
          onAction={handleSubscribe}
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b text-xs text-muted-foreground uppercase tracking-wider">
                  <th className="hidden sm:table-cell text-left px-5 py-3 font-medium">Platform</th>
                  <th className="text-left px-5 py-3 font-medium">Meeting</th>
                  <th className="text-left px-5 py-3 font-medium">Status</th>
                  <th className="text-left px-5 py-3 font-medium">Duration</th>
                  <th className="hidden lg:table-cell text-left px-5 py-3 font-medium">Participants</th>
                  <th className="hidden sm:table-cell text-left px-5 py-3 font-medium">Time</th>
                </tr>
              </thead>
              <tbody className="text-sm">
                {isLoadingMeetings ? (
                  <tr>
                    <td colSpan={6} className="px-5 py-12 text-center">
                      <Loader2 className="h-5 w-5 animate-spin mx-auto text-muted-foreground" />
                    </td>
                  </tr>
                ) : filteredMeetings.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-5 py-12 text-center">
                      <div className="flex flex-col items-center gap-2">
                        <Video className="h-8 w-8 text-muted-foreground/50" />
                        <p className="text-sm text-muted-foreground">
                          {searchQuery.trim() || platformFilter !== "all" || statusFilter !== "all"
                            ? "No meetings match your filters"
                            : "No meetings yet"}
                        </p>
                        {!searchQuery.trim() && platformFilter === "all" && statusFilter === "all" && !subscriptionRequired && (
                          <Button onClick={openJoinModal} size="sm" variant="outline" className="mt-2">
                            <Plus className="mr-2 h-3.5 w-3.5" />
                            Join your first meeting
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ) : (
                  filteredMeetings.map((meeting) => (
                    <MeetingRow key={meeting.id} meeting={meeting} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>
      )}

    </div>
  );
}

function MeetingRow({ meeting }: { meeting: Meeting }) {
  const router = useRouter();
  const statusConfig = getDetailedStatus(meeting.status, meeting.data);
  const displayTitle = meeting.data?.name || meeting.data?.title || meeting.platform_specific_id;
  const participants = meeting.data?.participants || [];

  return (
      <tr
        className="border-b border-border/50 hover:bg-muted/30 cursor-pointer transition-colors"
        onClick={() => router.push(`/meetings/${meeting.id}`)}
      >
        <td className="hidden sm:table-cell px-5 py-3">
          <PlatformIcon platform={meeting.platform} />
        </td>
        <td className="px-5 py-3">
          <span className="font-medium">{displayTitle}</span>
          {(meeting.data?.name || meeting.data?.title) && (
            <span className="block text-xs text-muted-foreground font-mono mt-0.5">
              {meeting.platform_specific_id}
            </span>
          )}
        </td>
        <td className="px-5 py-3">
          <span className="inline-flex items-center gap-1.5">
            <StatusDot status={meeting.status} />
            <span
              className={cn(
                "text-xs",
                (meeting.status === "completed") && "text-emerald-400",
                (meeting.status === "active" || meeting.status === "joining") && "text-emerald-400",
                (meeting.status === "awaiting_admission" || meeting.status === "stopping") && "text-amber-400",
                meeting.status === "failed" && "text-red-400"
              )}
            >
              {statusConfig.label}
            </span>
          </span>
        </td>
        <td className="px-5 py-3 text-muted-foreground">
          {formatDuration(meeting.start_time, meeting.end_time)}
        </td>
        <td className="hidden lg:table-cell px-5 py-3 text-muted-foreground text-xs">
          {participants.length > 0 ? (
            <span>
              {participants.slice(0, 2).join(", ")}
              {participants.length > 2 && ` +${participants.length - 2}`}
            </span>
          ) : (
            "—"
          )}
        </td>
        <td className="hidden sm:table-cell px-5 py-3 text-muted-foreground text-xs whitespace-nowrap">
          {meeting.start_time ? (
            <>
              {formatDate(meeting.start_time)}
              <span className="block text-[10px] text-muted-foreground/70">
                {formatDistanceToNow(new Date(meeting.start_time), { addSuffix: true })}
              </span>
            </>
          ) : meeting.created_at ? (
            formatDate(meeting.created_at)
          ) : (
            "—"
          )}
        </td>
      </tr>
  );
}
