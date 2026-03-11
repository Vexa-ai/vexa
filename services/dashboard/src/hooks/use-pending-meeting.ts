"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { consumePendingMeetingUrl } from "@/lib/pending-meeting";
import { parseMeetingInput } from "@/lib/parse-meeting-input";
import { vexaAPI } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useLiveStore } from "@/stores/live-store";
import { useMeetingsStore } from "@/stores/meetings-store";
import { getUserFriendlyError } from "@/lib/error-messages";
import type { CreateBotRequest } from "@/types/vexa";

export function usePendingMeeting() {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setActiveMeeting = useLiveStore((s) => s.setActiveMeeting);
  const setCurrentMeeting = useMeetingsStore((s) => s.setCurrentMeeting);
  const processedRef = useRef(false);

  useEffect(() => {
    if (!isAuthenticated || processedRef.current) return;

    const meetingUrl = consumePendingMeetingUrl();
    if (!meetingUrl) return;

    processedRef.current = true;

    const parsed = parseMeetingInput(meetingUrl);
    if (!parsed) {
      toast.error("The saved meeting URL is no longer valid");
      return;
    }

    const request: CreateBotRequest = {
      platform: parsed.platform,
      native_meeting_id: parsed.meetingId,
    };
    if (parsed.passcode) {
      request.passcode = parsed.passcode;
    }
    request.bot_name = "Vexa - Open Source Bot";

    toast.promise(
      vexaAPI.createBot(request).then((meeting) => {
        setActiveMeeting(meeting);
        setCurrentMeeting(meeting);
        router.push(`/meetings/${meeting.id}?apiView=1`);
        return meeting;
      }),
      {
        loading: "Joining your meeting...",
        success: "Bot is connecting to your meeting!",
        error: (err) => {
          const { title, description } = getUserFriendlyError(err);
          return `${title}${description ? `: ${description}` : ""}`;
        },
      }
    );
  }, [isAuthenticated, router, setActiveMeeting, setCurrentMeeting]);
}
