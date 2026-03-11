"use client";

import { useEffect, useState, useCallback } from "react";
import { formatDistanceToNow, format } from "date-fns";
import {
  Bot,
  RefreshCw,
  StopCircle,
  Play,
  Clock,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Loader2,
  Video,
  Users,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { vexaAPI } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import type { Meeting, MeetingStatus, Platform } from "@/types/vexa";

const STATUS_CONFIG: Record<MeetingStatus, { label: string; color: string; icon: React.ElementType }> = {
  requested: { label: "Requested", color: "bg-gray-100 text-gray-700", icon: Clock },
  joining: { label: "Joining", color: "bg-yellow-100 text-yellow-700", icon: Play },
  awaiting_admission: { label: "Waiting", color: "bg-orange-100 text-orange-700", icon: Clock },
  active: { label: "Active", color: "bg-green-100 text-green-700", icon: CheckCircle },
  stopping: { label: "Stopping", color: "bg-slate-100 text-slate-700", icon: Loader2 },
  completed: { label: "Completed", color: "bg-blue-100 text-blue-700", icon: CheckCircle },
  failed: { label: "Failed", color: "bg-red-100 text-red-700", icon: XCircle },
};

export default function AdminBotsPage() {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [runningBots, setRunningBots] = useState<Array<{ container_id: string; meeting_id: number; platform: string; native_meeting_id: string }>>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [stoppingBots, setStoppingBots] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async (showRefresh = false) => {
    if (showRefresh) setIsRefreshing(true);
    try {
      const [meetingsData, botsData] = await Promise.all([
        vexaAPI.getMeetings(),
        vexaAPI.getBotStatus().catch(() => ({ running_bots: [] })),
      ]);
      setMeetings(meetingsData);
      setRunningBots(botsData.running_bots || []);
    } catch (error) {
      console.error("Failed to fetch data:", error);
      toast.error("Failed to load data");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    // Auto-refresh every 10 seconds
    const interval = setInterval(() => fetchData(), 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleStopBot = useCallback(async (platform: Platform, nativeId: string) => {
    const key = `${platform}:${nativeId}`;

    // Prevent duplicate requests
    if (stoppingBots.has(key)) {
      console.log(`Already stopping bot ${key}, ignoring duplicate request`);
      return;
    }

    setStoppingBots(prev => new Set(prev).add(key));
    try {
      await vexaAPI.stopBot(platform, nativeId);
      toast.success("Bot stopped successfully");
      // Wait a bit before refreshing to let the backend update
      setTimeout(() => fetchData(true), 500);
    } catch (error) {
      toast.error("Failed to stop bot", {
        description: (error as Error).message,
      });
    } finally {
      setStoppingBots(prev => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, [stoppingBots, fetchData]);

  const activeMeetings = meetings.filter(m =>
    m.status === "requested" || m.status === "joining" || m.status === "awaiting_admission" || m.status === "active"
  );
  const completedMeetings = meetings.filter(m => m.status === "completed");
  const failedMeetings = meetings.filter(m => m.status === "failed");

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <Skeleton className="h-8 w-32" />
            <Skeleton className="h-4 w-48 mt-2" />
          </div>
          <Skeleton className="h-10 w-10" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-96" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground flex items-center gap-3">
            <Bot className="h-8 w-8" />
            Bots & Meetings
          </h1>
          <p className="text-muted-foreground">
            Monitor and manage transcription bots
          </p>
        </div>
        <Button
          variant="outline"
          size="icon"
          onClick={() => fetchData(true)}
          disabled={isRefreshing}
        >
          <RefreshCw className={cn("h-4 w-4", isRefreshing && "animate-spin")} />
        </Button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-green-100 dark:bg-green-950 flex items-center justify-center">
                <Play className="h-5 w-5 text-green-600" />
              </div>
              <div>
                <p className="text-2xl font-bold">{runningBots.length}</p>
                <p className="text-sm text-muted-foreground">Running Bots</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-orange-100 dark:bg-orange-950 flex items-center justify-center">
                <Clock className="h-5 w-5 text-orange-600" />
              </div>
              <div>
                <p className="text-2xl font-bold">{activeMeetings.length}</p>
                <p className="text-sm text-muted-foreground">Active Sessions</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-blue-100 dark:bg-blue-950 flex items-center justify-center">
                <CheckCircle className="h-5 w-5 text-blue-600" />
              </div>
              <div>
                <p className="text-2xl font-bold">{completedMeetings.length}</p>
                <p className="text-sm text-muted-foreground">Completed</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-red-100 dark:bg-red-950 flex items-center justify-center">
                <XCircle className="h-5 w-5 text-red-600" />
              </div>
              <div>
                <p className="text-2xl font-bold">{failedMeetings.length}</p>
                <p className="text-sm text-muted-foreground">Failed</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Running Bots Alert */}
      {runningBots.length > 0 && (
        <Card className="border-green-200 bg-green-50/50 dark:bg-green-950/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-green-700 dark:text-green-400">
              <Play className="h-5 w-5" />
              Running Bots ({runningBots.length})
            </CardTitle>
            <CardDescription>
              Currently active bot containers
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {runningBots.map((bot) => (
                <div
                  key={bot.container_id}
                  className="flex items-center justify-between p-3 bg-white dark:bg-gray-900 rounded-lg border"
                >
                  <div className="flex items-center gap-3">
                    <div className="h-8 w-8 rounded-full bg-green-500 flex items-center justify-center">
                      <Bot className="h-4 w-4 text-white" />
                    </div>
                    <div>
                      <p className="font-medium font-mono text-sm">{bot.native_meeting_id}</p>
                      <p className="text-xs text-muted-foreground">
                        {bot.platform} • Container: {bot.container_id.slice(0, 12)}
                      </p>
                    </div>
                  </div>
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={stoppingBots.has(`${bot.platform}:${bot.native_meeting_id}`)}
                      >
                        {stoppingBots.has(`${bot.platform}:${bot.native_meeting_id}`) ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <>
                            <StopCircle className="mr-1 h-4 w-4" />
                            Stop
                          </>
                        )}
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Stop this bot?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will stop the transcription bot for meeting {bot.native_meeting_id}.
                          The meeting will be marked as completed.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          className="bg-destructive hover:bg-destructive/90"
                          onClick={() => handleStopBot(bot.platform as Platform, bot.native_meeting_id)}
                        >
                          Stop Bot
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* All Meetings Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Video className="h-5 w-5" />
            All Meetings ({meetings.length})
          </CardTitle>
          <CardDescription>
            History of all bot sessions
          </CardDescription>
        </CardHeader>
        <CardContent>
          {meetings.length === 0 ? (
            <div className="text-center py-12">
              <Video className="h-12 w-12 mx-auto text-muted-foreground/50 mb-4" />
              <h3 className="text-lg font-medium mb-2">No meetings yet</h3>
              <p className="text-muted-foreground">
                Meetings will appear here when bots join calls
              </p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Meeting ID</TableHead>
                    <TableHead>Platform</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {meetings.map((meeting) => {
                    const statusConfig = STATUS_CONFIG[meeting.status];
                    const StatusIcon = statusConfig.icon;
                    const isActive = meeting.status === "requested" || meeting.status === "joining" ||
                                   meeting.status === "awaiting_admission" || meeting.status === "active";
                    const isStopping = stoppingBots.has(`${meeting.platform}:${meeting.platform_specific_id}`);

                    const duration = meeting.start_time && meeting.end_time
                      ? Math.round((new Date(meeting.end_time).getTime() - new Date(meeting.start_time).getTime()) / 60000)
                      : null;

                    return (
                      <TableRow key={meeting.id}>
                        <TableCell className="font-mono text-sm">
                          {meeting.platform_specific_id}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="capitalize">
                            {meeting.platform.replace("_", " ")}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Badge className={cn("gap-1", statusConfig.color)}>
                            <StatusIcon className="h-3 w-3" />
                            {statusConfig.label}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDistanceToNow(new Date(meeting.created_at), { addSuffix: true })}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {duration ? `${duration} min` : "-"}
                        </TableCell>
                        <TableCell className="text-right">
                          {isActive && (
                            <AlertDialog>
                              <AlertDialogTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="text-destructive hover:text-destructive"
                                  disabled={isStopping}
                                >
                                  {isStopping ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <StopCircle className="h-4 w-4" />
                                  )}
                                </Button>
                              </AlertDialogTrigger>
                              <AlertDialogContent>
                                <AlertDialogHeader>
                                  <AlertDialogTitle>Stop this bot?</AlertDialogTitle>
                                  <AlertDialogDescription>
                                    This will stop the transcription bot for this meeting.
                                  </AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                                  <AlertDialogAction
                                    className="bg-destructive hover:bg-destructive/90"
                                    onClick={() => handleStopBot(meeting.platform, meeting.platform_specific_id)}
                                  >
                                    Stop Bot
                                  </AlertDialogAction>
                                </AlertDialogFooter>
                              </AlertDialogContent>
                            </AlertDialog>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
