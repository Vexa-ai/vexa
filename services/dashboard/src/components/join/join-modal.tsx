"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Video, Loader2, Sparkles, Globe, ChevronDown, Mic } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { vexaAPI, VexaAPIError } from "@/lib/api";
import { useLiveStore } from "@/stores/live-store";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import type { Platform, CreateBotRequest } from "@/types/vexa";
import { LanguagePicker } from "@/components/language-picker";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { getUserFriendlyError } from "@/lib/error-messages";
import { getWebappUrl } from "@/lib/docs/webapp-url";
import { parseMeetingInput } from "@/lib/parse-meeting-input";
import { DocsLink } from "@/components/docs/docs-link";
import { useAuthStore } from "@/stores/auth-store";
import { shouldTriggerZoomOAuth, startZoomOAuth } from "@/lib/zoom-oauth-client";


export function JoinModal() {
  const router = useRouter();
  const { isOpen, closeModal } = useJoinModalStore();
  const { setActiveMeeting } = useLiveStore();
  const { setCurrentMeeting } = useMeetingsStore();
  const { config } = useRuntimeConfig();
  const user = useAuthStore((state) => state.user);

  const [meetingInput, setMeetingInput] = useState("");
  const [platform, setPlatform] = useState<Platform>("google_meet");
  const [language, setLanguage] = useState("auto");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [transcribeEnabled, setTranscribeEnabled] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [botName, setBotName] = useState("");
  const [passcode, setPasscode] = useState("");

  // Default is "auto" so we don't send a language and the transcription service can auto-detect.
  // (Previously we set getBrowserLanguage() here, which sent e.g. "en" and skipped detection.)

  // Reset form when modal closes
  useEffect(() => {
    if (!isOpen) {
      setMeetingInput("");
      setPlatform("google_meet");
      setIsSubmitting(false);
      setTranscribeEnabled(true);
      setShowAdvanced(false);
      setBotName("");
      setPasscode("");
    }
  }, [isOpen]);

  // Parse input and auto-detect platform
  const parsedInput = useMemo(() => {
    return parseMeetingInput(meetingInput);
  }, [meetingInput]);

  // Update platform and passcode when detected from URL
  useEffect(() => {
    if (parsedInput) {
      setPlatform(parsedInput.platform);
      // Auto-populate passcode if detected from URL
      if (parsedInput.passcode) {
        setPasscode(parsedInput.passcode);
      }
    }
  }, [parsedInput]);

  const isValid = parsedInput !== null;

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();

    if (!parsedInput) {
      toast.error("Invalid meeting", {
        description: "Please enter a valid Google Meet, Zoom, or Teams URL or meeting code",
      });
      return;
    }

    // Validate Teams passcode requirement and prepare final passcode
    // Use passcode from parsed URL first, then fall back to manually entered passcode
    const finalPasscode = parsedInput.passcode || passcode.trim() || undefined;
    if (parsedInput.platform === "teams" && !finalPasscode) {
      toast.error("Passcode required", {
        description: "Microsoft Teams meetings require a passcode",
      });
      return;
    }

    setIsSubmitting(true);

    const request: CreateBotRequest = {
        platform: parsedInput.platform,
        native_meeting_id: parsedInput.meetingId,
      };

    // Add passcode for Teams and Zoom meetings
    if ((parsedInput.platform === "teams" || parsedInput.platform === "zoom") && finalPasscode) {
      request.passcode = finalPasscode;
    }

    // Pass original URL for Teams to preserve exact domain
    if (parsedInput.originalUrl) {
      request.meeting_url = parsedInput.originalUrl;
    }

    // Set bot name - use custom name or configured default
    request.bot_name = botName.trim() || config?.defaultBotName || "Vexa - Open Source Bot";

    if (language && language !== "auto") {
      request.language = language;
    }

    if (!transcribeEnabled) {
      request.transcribe_enabled = false;
    }

    try {
      const meeting = await vexaAPI.createBot(request);

      toast.success("Bot joining meeting", {
        description: "The transcription bot is connecting...",
      });

      // Set meeting in both stores to ensure fresh data is used immediately
      setActiveMeeting(meeting);
      setCurrentMeeting(meeting);
      closeModal();

      // Navigate to the meeting page
      router.push(`/meetings/${meeting.id}`);
    } catch (error) {
      console.error("Failed to create bot:", error);

      // Subscription required — redirect to pricing
      if (error instanceof VexaAPIError && error.status === 402) {
        toast.error("Subscription required", {
          description: "Subscribe to a plan to create bots.",
          action: {
            label: "View Plans",
            onClick: () => window.open(`${getWebappUrl()}/pricing`, "_blank"),
          },
        });
        return;
      }

      if (
        shouldTriggerZoomOAuth(error, request.platform) &&
        request.platform === "zoom" &&
        user?.email
      ) {
        try {
          toast.info("Zoom authentication required", {
            description:
              "Redirecting to Zoom. Sign in with the Zoom account that owns or is allowed to use the Vexa app to avoid \"Application not found\".",
          });
          await startZoomOAuth({
            userEmail: user.email,
            pendingRequest: request,
            returnTo: "/meetings",
          });
          return;
        } catch (oauthError) {
          toast.error("Failed to start Zoom authentication", {
            description: (oauthError as Error).message,
          });
        }
      }

      const { title, description } = getUserFriendlyError(error as Error);
      toast.error(title, { description });
    } finally {
      setIsSubmitting(false);
    }
  }, [parsedInput, passcode, botName, language, transcribeEnabled, config, setActiveMeeting, setCurrentMeeting, closeModal, router, user]);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && closeModal()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center">
              <Video className="h-4 w-4 text-primary-foreground" />
            </div>
            Join a Meeting
          </DialogTitle>
          <DialogDescription>
            Paste a Google Meet, Zoom, or Teams URL to start transcribing automatically
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 mt-2">
          {/* Meeting Input */}
          <div className="space-y-2">
            <Label htmlFor="meetingInput" className="sr-only">
              Meeting URL or Code
            </Label>
            <div className="relative">
              {/* Platform Icon - appears when detected */}
              {parsedInput && (
                <div className="absolute left-3 top-1/2 -translate-y-1/2 z-10 animate-fade-in">
                  {parsedInput.platform === "google_meet" ? (
                    <div className="h-6 w-6 rounded-md bg-green-500 flex items-center justify-center shadow-sm">
                      <svg className="h-4 w-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                      </svg>
                    </div>
                  ) : parsedInput.platform === "zoom" ? (
                    <div className="h-6 w-6 rounded-md bg-blue-500 flex items-center justify-center shadow-sm">
                      <Video className="h-4 w-4 text-white" />
                    </div>
                  ) : (
                    <div className="h-6 w-6 rounded-md bg-[#5059C9] flex items-center justify-center shadow-sm">
                      <svg className="h-4 w-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19.98 7.89A2.14 2.14 0 1 0 17.84 10V7.89h2.14zm-5.27 0A2.14 2.14 0 1 0 12.58 10V7.89h2.13zM12.58 14.5h-1.11v-1.8h1.11zm4.13 0h-1.11v-1.8h1.11zM21 11.36v5.5a3 3 0 0 1-3 3h-3.86v-4.5H12.5v4.5H8.64v-4.5h-1.78a3 3 0 0 1-3-3v-5.5a3 3 0 0 1 3-3h11.14a3 3 0 0 1 3 3z"/>
                      </svg>
                    </div>
                  )}
                </div>
              )}
              <Input
                id="meetingInput"
                placeholder="Paste meeting URL (Google Meet, Zoom, or Teams)..."
                value={meetingInput}
                onChange={(e) => setMeetingInput(e.target.value)}
                className={cn(
                  "h-12 text-base pr-12 font-mono transition-all",
                  parsedInput ? "pl-12" : "pl-4",
                  meetingInput && (
                    isValid
                      ? parsedInput?.platform === "google_meet"
                        ? "border-green-500 focus-visible:ring-green-500/20"
                        : parsedInput?.platform === "zoom"
                        ? "border-blue-500 focus-visible:ring-blue-500/20"
                        : "border-[#5059C9] focus-visible:ring-[#5059C9]/20"
                      : "border-orange-500 focus-visible:ring-orange-500/20"
                  )
                )}
                autoFocus
                autoComplete="off"
              />
              {/* Valid indicator */}
              {meetingInput && isValid && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  <div className={cn(
                    "h-6 w-6 rounded-full flex items-center justify-center animate-fade-in",
                    parsedInput?.platform === "google_meet"
                      ? "bg-green-100 dark:bg-green-950"
                      : parsedInput?.platform === "zoom"
                      ? "bg-blue-100 dark:bg-blue-950"
                      : "bg-indigo-100 dark:bg-indigo-950"
                  )}>
                    <svg className={cn(
                      "h-4 w-4",
                      parsedInput?.platform === "google_meet"
                        ? "text-green-600 dark:text-green-400"
                        : parsedInput?.platform === "zoom"
                        ? "text-blue-600 dark:text-blue-400"
                        : "text-[#5059C9]"
                    )} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                </div>
              )}
            </div>

            {/* Detected platform indicator */}
            {parsedInput && (
              <div className="flex items-center gap-2 text-sm animate-fade-in">
                <span className={cn(
                  "inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium",
                  parsedInput.platform === "google_meet"
                    ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300"
                    : parsedInput.platform === "zoom"
                    ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                    : "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                )}>
                  {parsedInput.platform === "google_meet" ? (
                    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                    </svg>
                  ) : parsedInput.platform === "zoom" ? (
                    <Video className="h-3 w-3" />
                  ) : (
                    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M19.98 7.89A2.14 2.14 0 1 0 17.84 10V7.89h2.14zm-5.27 0A2.14 2.14 0 1 0 12.58 10V7.89h2.13zM12.58 14.5h-1.11v-1.8h1.11zm4.13 0h-1.11v-1.8h1.11zM21 11.36v5.5a3 3 0 0 1-3 3h-3.86v-4.5H12.5v4.5H8.64v-4.5h-1.78a3 3 0 0 1-3-3v-5.5a3 3 0 0 1 3-3h11.14a3 3 0 0 1 3 3z"/>
                    </svg>
                  )}
                  {parsedInput.platform === "google_meet" ? "Google Meet" : parsedInput.platform === "zoom" ? "Zoom" : "Microsoft Teams"}
                </span>
                <span className="font-mono text-xs bg-muted px-2 py-1 rounded-md truncate max-w-[200px]">
                  {parsedInput.meetingId}
                </span>
              </div>
            )}
          </div>

          {/* Language Selection - only shown when transcription is enabled */}
          {transcribeEnabled && <div className="space-y-2">
            <Label htmlFor="language" className="text-sm flex items-center gap-2">
              <Globe className="h-3.5 w-3.5" />
              Transcription Language
            </Label>
            <LanguagePicker
              value={language}
              onValueChange={setLanguage}
              triggerClassName="h-10 w-full justify-between"
            />
            {language === "auto" && (
              <p className="text-xs text-muted-foreground">
                Auto-detect: the service will detect the language when the meeting starts. You can change it anytime from the meeting page.
              </p>
            )}
          </div>}

          {/* Transcription Toggle */}
          <div className="flex items-center justify-between">
            <Label htmlFor="transcribe" className="text-sm flex items-center gap-2 cursor-pointer">
              <Mic className="h-3.5 w-3.5" />
              Real-time Transcription
            </Label>
            <Switch
              id="transcribe"
              checked={transcribeEnabled}
              onCheckedChange={setTranscribeEnabled}
            />
          </div>
          {!transcribeEnabled && (
            <p className="text-xs text-muted-foreground -mt-2">
              Bot will record audio only. You can transcribe later from the meeting page.
            </p>
          )}

          {/* Advanced Options Toggle */}
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronDown className={cn(
              "h-4 w-4 transition-transform",
              showAdvanced && "rotate-180"
            )} />
            Advanced options
          </button>

          {/* Advanced Options */}
          {showAdvanced && (
            <div className="space-y-4 animate-fade-in pt-2 border-t">
              {/* Bot Name */}
              <div className="space-y-2">
                <Label htmlFor="botName" className="text-sm">
                  Bot Name (optional)
                </Label>
                <Input
                  id="botName"
                  placeholder="Meeting Assistant"
                  value={botName}
                  onChange={(e) => setBotName(e.target.value)}
                  className="h-10"
                />
              </div>

              {/* Passcode for Teams and Zoom */}
              {(platform === "teams" || platform === "zoom") && (
                <div className="space-y-2">
                  <Label htmlFor="passcode" className="text-sm">
                    Passcode {platform === "teams" ? "(required for Teams)" : "(optional for Zoom)"}
                  </Label>
                  <Input
                    id="passcode"
                    placeholder="Enter meeting passcode"
                    value={passcode}
                    onChange={(e) => setPasscode(e.target.value)}
                    className="h-10"
                  />
                </div>
              )}
            </div>
          )}

          {/* Submit Button */}
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              className={cn(
                "flex-1 h-12 text-base transition-all duration-300",
                isValid && !isSubmitting && "shadow-lg shadow-primary/25"
              )}
              disabled={isSubmitting || !isValid}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                  Connecting...
                </>
              ) : (
                <>
                  <Sparkles className="mr-2 h-5 w-5" />
                  {transcribeEnabled ? "Start Transcription" : "Start Recording"}
                </>
              )}
            </Button>
            <DocsLink href="/docs/rest/bots#create-bot" />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
