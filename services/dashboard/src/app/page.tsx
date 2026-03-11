"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Video, Clock, TrendingUp, Plus, ArrowRight } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { MeetingList } from "@/components/meetings/meeting-list";
import { ErrorState } from "@/components/ui/error-state";
import { MCPConfigButton } from "@/components/mcp/mcp-config-button";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { usePendingMeeting } from "@/hooks/use-pending-meeting";

export default function DashboardPage() {
  // Auto-join pending meeting from onboarding flow
  usePendingMeeting();

  const { meetings, isLoadingMeetings, fetchMeetings, error } = useMeetingsStore();
  const openJoinModal = useJoinModalStore((state) => state.openModal);

  useEffect(() => {
    fetchMeetings();
  }, [fetchMeetings]);

  // Calculate stats
  const totalMeetings = meetings.length;
  const activeMeetings = meetings.filter((m) => m.status === "active").length;

  // Get meetings from this week
  const oneWeekAgo = new Date();
  oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
  const thisWeekMeetings = meetings.filter(
    (m) => new Date(m.created_at) >= oneWeekAgo
  ).length;

  // Get recent meetings (last 5)
  const recentMeetings = meetings.slice(0, 5);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Overview of your meeting transcriptions
          </p>
        </div>
        <div className="flex items-center gap-2">
          <MCPConfigButton />
          <Button variant="outline" asChild>
            <a
              href="https://discord.gg/KXpwveJewr"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2"
            >
              <img
                src="/icons/icons8-discord-100.png"
                alt="Discord"
                width={20}
                height={20}
                className="object-contain flex-shrink-0 dark:invert"
              />
              Support
            </a>
          </Button>
          <Button variant="outline" asChild>
            <a
              href="https://github.com/Vexa-ai/vexa-dashboard"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2"
            >
              <img
                src="/icons/icons8-github-120.png"
                alt="GitHub"
                width={20}
                height={20}
                className="object-contain flex-shrink-0 dark:invert"
              />
              Fork me!
            </a>
          </Button>
          <Button onClick={openJoinModal}>
            <Plus className="mr-2 h-4 w-4" />
            Join Meeting
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Meetings</CardTitle>
            <Video className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold tracking-[-0.02em]">{totalMeetings}</div>
            <p className="text-xs text-muted-foreground">
              All recorded meetings
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">This Week</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold tracking-[-0.02em]">{thisWeekMeetings}</div>
            <p className="text-xs text-muted-foreground">
              Meetings in the last 7 days
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Now</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold tracking-[-0.02em]">{activeMeetings}</div>
            <p className="text-xs text-muted-foreground">
              Currently recording
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Recent Meetings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Recent Meetings</CardTitle>
              <CardDescription>Your latest transcribed meetings</CardDescription>
            </div>
            {meetings.length > 5 && (
              <Button variant="ghost" size="sm" asChild>
                <Link href="/meetings">
                  View all
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {error ? (
            <ErrorState
              error={error}
              onRetry={fetchMeetings}
            />
          ) : (
            <MeetingList
              meetings={recentMeetings}
              isLoading={isLoadingMeetings}
              limit={5}
              emptyMessage="No meetings yet. Join your first meeting to get started!"
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
