"use client";

import { useEffect, useMemo } from "react";
import { StopCircle, Wifi, WifiOff, Loader2, Users, Clock } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { TranscriptSegment } from "@/components/transcript/transcript-segment";
import { useVexaWebSocket } from "@/hooks/use-vexa-websocket";
import { useLiveStore } from "@/stores/live-store";
import { vexaAPI } from "@/lib/api";
import type { Platform } from "@/types/vexa";
import { PLATFORM_CONFIG, MEETING_STATUS_CONFIG, getSpeakerColor } from "@/types/vexa";
import { cn } from "@/lib/utils";
import { DocsLink } from "@/components/docs/docs-link";

interface LiveSessionProps {
  platform: Platform;
  nativeId: string;
  onEnd?: () => void;
}

export function LiveSession({ platform, nativeId, onEnd }: LiveSessionProps) {
  const {
    activeMeeting,
    liveTranscripts,
    botStatus,
    clearLiveSession,
  } = useLiveStore();

  const { isConnecting, isConnected, error } = useVexaWebSocket({
    platform,
    nativeId,
    autoConnect: true,
    onError: (err) => {
      toast.error("WebSocket error", { description: err });
    },
  });

  // Get unique speakers in order of appearance
  const speakerOrder = useMemo(() => {
    const speakers: string[] = [];
    for (const segment of liveTranscripts) {
      if (!speakers.includes(segment.speaker)) {
        speakers.push(segment.speaker);
      }
    }
    return speakers;
  }, [liveTranscripts]);

  const handleStopBot = async () => {
    try {
      await vexaAPI.stopBot(platform, nativeId);
      toast.success("Bot stopped", {
        description: "The transcription bot has left the meeting",
      });
      clearLiveSession();
      onEnd?.();
    } catch (error) {
      toast.error("Failed to stop bot", {
        description: (error as Error).message,
      });
    }
  };

  const platformConfig = PLATFORM_CONFIG[platform];
  const statusConfig = botStatus ? MEETING_STATUS_CONFIG[botStatus] : null;

  // Check if meeting is still active
  const isActive = botStatus === "active" || botStatus === "joining" || botStatus === "awaiting_admission";

  return (
    <div className="space-y-4">
      {/* Status Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {/* Live indicator */}
              {isActive && (
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
                </span>
              )}
              <CardTitle className="text-lg">
                {isActive ? "Live Session" : "Session Ended"}
              </CardTitle>
            </div>

            {isActive && (
              <div className="flex items-center">
                <Button variant="destructive" size="sm" onClick={handleStopBot}>
                  <StopCircle className="h-4 w-4 mr-2" />
                  Stop Bot
                </Button>
                <DocsLink href="/docs/rest/bots#stop-bot" />
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3">
            {/* Platform */}
            <Badge variant="outline" className={cn(platformConfig.bgColor, platformConfig.textColor)}>
              {platformConfig.name}
            </Badge>

            {/* Meeting ID */}
            <Badge variant="secondary" className="font-mono">
              {nativeId}
            </Badge>

            {/* Bot Status */}
            {statusConfig && (
              <Badge className={cn(statusConfig.bgColor, statusConfig.color)}>
                {statusConfig.label}
              </Badge>
            )}

            {/* WebSocket Status */}
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
              {isConnecting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Connecting...</span>
                </>
              ) : isConnected ? (
                <>
                  <Wifi className="h-4 w-4 text-green-500" />
                  <span>Connected</span>
                </>
              ) : (
                <>
                  <WifiOff className="h-4 w-4 text-red-500" />
                  <span>Disconnected</span>
                </>
              )}
            </div>

            {/* Participants count */}
            {speakerOrder.length > 0 && (
              <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Users className="h-4 w-4" />
                <span>{speakerOrder.length} speaker{speakerOrder.length !== 1 ? "s" : ""}</span>
              </div>
            )}
          </div>

          {error && (
            <p className="mt-3 text-sm text-red-500">{error}</p>
          )}
        </CardContent>
      </Card>

      {/* Live Transcript */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Live Transcript
            {liveTranscripts.length > 0 && (
              <Badge variant="secondary">{liveTranscripts.length} segments</Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[400px] pr-4">
            {liveTranscripts.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full py-12 text-center">
                {isActive ? (
                  <>
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mb-4" />
                    <p className="text-muted-foreground">
                      Waiting for speech to transcribe...
                    </p>
                    <p className="text-sm text-muted-foreground mt-1">
                      Transcripts will appear here in real-time
                    </p>
                  </>
                ) : (
                  <p className="text-muted-foreground">No transcript available</p>
                )}
              </div>
            ) : (
              <div className="space-y-1">
                {liveTranscripts.map((segment, index) => (
                  <TranscriptSegment
                    key={segment.id || `${segment.absolute_start_time}-${index}`}
                    segment={segment}
                    speakerColor={getSpeakerColor(segment.speaker, speakerOrder)}
                  />
                ))}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
