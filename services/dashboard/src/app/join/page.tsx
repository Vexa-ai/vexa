"use client";

import { useState } from "react";
import { JoinForm } from "@/components/join/join-form";
import { LiveSession } from "@/components/join/live-session";
import type { Platform } from "@/types/vexa";

interface ActiveSession {
  meetingId: string;
  platform: Platform;
  nativeId: string;
}

export default function JoinPage() {
  const [activeSession, setActiveSession] = useState<ActiveSession | null>(null);

  const handleJoinSuccess = (meetingId: string, platform: Platform, nativeId: string) => {
    setActiveSession({ meetingId, platform, nativeId });
  };

  const handleSessionEnd = () => {
    setActiveSession(null);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Join Meeting</h1>
        <p className="text-muted-foreground">
          Send a transcription bot to record and transcribe your meeting in real-time
        </p>
      </div>

      {/* Content */}
      <div className="max-w-2xl">
        {activeSession ? (
          <LiveSession
            platform={activeSession.platform}
            nativeId={activeSession.nativeId}
            onEnd={handleSessionEnd}
          />
        ) : (
          <JoinForm onSuccess={handleJoinSuccess} />
        )}
      </div>
    </div>
  );
}
