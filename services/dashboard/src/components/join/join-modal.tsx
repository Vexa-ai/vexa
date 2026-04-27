"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Video, Loader2, Sparkles, Globe, Mic, Monitor, UserCheck } from "lucide-react";
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
import { withBasePath } from "@/lib/base-path";


export function JoinModal() {
  const router = useRouter();
  const { isOpen, closeModal } = useJoinModalStore();
  const { setActiveMeeting } = useLiveStore();
  const { setCurrentMeeting } = useMeetingsStore();
  const { config } = useRuntimeConfig();
  const user = useAuthStore((state) => state.user);

  const [mode, setMode] = useState<"meeting" | "browser">("meeting");
  const [meetingInput, setMeetingInput] = useState("");
  const [platform, setPlatform] = useState<Platform>("google_meet");
  const [language, setLanguage] = useState("auto");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [transcribeEnabled, setTranscribeEnabled] = useState(true);
  const [botName, setBotName] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("vexa-join-bot-name") || "Vexa";
    }
    return "Vexa";
  });
  const [passcode, setPasscode] = useState("");
  const [authenticated, setAuthenticated] = useState(false);

  // Persist bot name and language to localStorage
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("vexa-join-bot-name", botName);
    }
  }, [botName]);
  useEffect(() => {
    if (typeof window !== "undefined") {
    }
  }, [language]);

  // Reset form when modal closes (preserve bot name and languages)
  useEffect(() => {
    if (!isOpen) {
      setMode("meeting");
      setMeetingInput("");
      setPlatform("google_meet");
      setIsSubmitting(false);
      setTranscribeEnabled(true);
      setPasscode("");
      setAuthenticated(false);
    }
  }, [isOpen]);

  // Parse input and auto-detect platform
  const parsedInput = useMemo(() => {
    return parseMeetingInput(meetingInput);
  }, [meetingInput]);

  // Update platform and passcode when detected from URL.
  // platformNeeded URLs (white-label / enterprise) leave platform at
  // its current value; user must pick.
  useEffect(() => {
    if (parsedInput && parsedInput.platform) {
      setPlatform(parsedInput.platform);
      if (parsedInput.passcode) {
        setPasscode(parsedInput.passcode);
      }
    } else if (parsedInput && parsedInput.passcode) {
      // platformNeeded but passcode extractable
      setPasscode(parsedInput.passcode);
    }
  }, [parsedInput]);

  // Valid request shapes:
  //   (a) parsed canonical (platform detected from URL/code)
  //   (b) parsed platformNeeded + user picked a platform
  const isValid = parsedInput !== null && (
    !parsedInput.platformNeeded || (parsedInput.platformNeeded && platform !== null)
  );

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();

    if (!parsedInput) {
      toast.error("Invalid meeting", {
        description: "Please enter a valid Google Meet, Zoom, or Teams URL or meeting code",
      });
      return;
    }

    // platformNeeded: user-supplied platform is canonical; parser couldn't auto-detect
    // (white-label / enterprise URL like Linux Foundation Zoom). Use the picked
    // platform in the request.
    const effectivePlatform = parsedInput.platform || platform;
    if (parsedInput.platformNeeded && !effectivePlatform) {
      toast.error("Platform required", {
        description: "This URL doesn't match a known meeting platform. Please select Google Meet, Zoom, or Teams.",
      });
      return;
    }

    const finalPasscode = parsedInput.passcode || passcode.trim() || undefined;
    if (effectivePlatform === "teams" && !finalPasscode) {
      toast.error("Passcode required", {
        description: "Microsoft Teams meetings require a passcode",
      });
      return;
    }

    setIsSubmitting(true);

    // Path 3 (URL + platform): when parser identified platform, use parsed
    // meetingId. Otherwise (platformNeeded), send meeting_url + platform; backend
    // synthesizes/extracts native_meeting_id best-effort.
    const request: CreateBotRequest = {
        platform: effectivePlatform!,
        native_meeting_id: parsedInput.meetingId || "",
      };

    if ((effectivePlatform === "teams" || effectivePlatform === "zoom") && finalPasscode) {
      request.passcode = finalPasscode;
    }

    if (parsedInput.originalUrl) {
      request.meeting_url = parsedInput.originalUrl;
    }

    request.bot_name = botName.trim() || config?.defaultBotName || "Vexa";

    if (language && language !== "auto") {
      request.language = language;
    }

    if (!transcribeEnabled) {
      request.transcribe_enabled = false;
    }

    if (authenticated) {
      request.authenticated = true;
    }

    try {
      const meeting = await vexaAPI.createBot(request);

      toast.success("Bot joining meeting", {
        description: "The transcription bot is connecting...",
      });

      setActiveMeeting(meeting);
      setCurrentMeeting(meeting);
      closeModal();

      router.push(`/meetings/${meeting.id}`);
    } catch (error) {
      console.error("Failed to create bot:", error);

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
  }, [parsedInput, passcode, botName, language, transcribeEnabled, authenticated, config, setActiveMeeting, setCurrentMeeting, closeModal, router, user]);

  const handleBrowserSession = useCallback(async () => {
    setIsSubmitting(true);
    try {
      const body: Record<string, string> = { mode: "browser_session" };
      try {
        const git = JSON.parse(localStorage.getItem("vexa-browser-git") || "{}");
        if (git.repo && git.token) {
          body.workspaceGitRepo = git.repo;
          body.workspaceGitToken = git.token;
          body.workspaceGitBranch = git.branch || "main";
        }
      } catch {}
      const response = await fetch(withBasePath("/api/vexa/bots"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Failed" }));
        throw new Error(err.detail || "Failed to create browser session");
      }
      const meeting = await response.json();
      toast.success("Browser session starting...");
      closeModal();
      setTimeout(() => router.push(`/meetings/${meeting.id}`), 2000);
    } catch (error) {
      const { title, description } = getUserFriendlyError(error as Error);
      toast.error(title, { description });
    } finally {
      setIsSubmitting(false);
    }
  }, [closeModal, router]);

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

        {/* Mode toggle */}
        <div className="flex gap-1 p-1 bg-muted rounded-lg mt-2">
          <button
            type="button"
            className={cn(
              "flex-1 flex items-center justify-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === "meeting" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
            onClick={() => setMode("meeting")}
          >
            <Video className="h-3.5 w-3.5" />
            Meeting
          </button>
          <button
            type="button"
            className={cn(
              "flex-1 flex items-center justify-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === "browser" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
            onClick={() => setMode("browser")}
          >
            <Monitor className="h-3.5 w-3.5" />
            Browser
          </button>
        </div>

        {mode === "browser" ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Remote browser with VNC, CDP, and SSH. Configure git workspace in Profile settings.
            </p>
            <Button
              className="w-full h-12 text-base"
              onClick={handleBrowserSession}
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Monitor className="mr-2 h-5 w-5" />
                  Start Browser Session
                </>
              )}
            </Button>
          </div>
        ) : (

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Meeting Input */}
          <div className="space-y-2">
            <Label htmlFor="meetingInput" className="sr-only">
              Meeting URL or Code
            </Label>
            <div className="relative">
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

            {/* v0.10.5 — platformNeeded fallback for white-label / enterprise URLs */}
            {parsedInput && parsedInput.platformNeeded && (
              <div className="space-y-2 animate-fade-in rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  <span className="font-semibold">URL recognized as a meeting link, but vendor isn&apos;t auto-detected.</span> Pick the platform — the bot will navigate the URL directly.
                </p>
                <fieldset className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Select meeting platform">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={platform === "google_meet"}
                    onClick={() => setPlatform("google_meet")}
                    className={cn(
                      "flex items-center justify-center gap-2 px-3 py-2 rounded-md border-2 text-xs font-medium transition-all",
                      platform === "google_meet"
                        ? "border-green-500 bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300"
                        : "border-muted hover:border-green-500/50"
                    )}
                  >
                    Google Meet
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={platform === "zoom"}
                    onClick={() => setPlatform("zoom")}
                    className={cn(
                      "flex items-center justify-center gap-2 px-3 py-2 rounded-md border-2 text-xs font-medium transition-all",
                      platform === "zoom"
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300"
                        : "border-muted hover:border-blue-500/50"
                    )}
                  >
                    Zoom
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={platform === "teams"}
                    onClick={() => setPlatform("teams")}
                    className={cn(
                      "flex items-center justify-center gap-2 px-3 py-2 rounded-md border-2 text-xs font-medium transition-all",
                      platform === "teams"
                        ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300"
                        : "border-muted hover:border-indigo-500/50"
                    )}
                  >
                    Microsoft Teams
                  </button>
                </fieldset>
              </div>
            )}

            {parsedInput && !parsedInput.platformNeeded && (
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

          {/* Bot Name */}
          <div className="space-y-2">
            <Label htmlFor="botName" className="text-sm">
              Bot Name
            </Label>
            <Input
              id="botName"
              placeholder="Vexa"
              value={botName}
              onChange={(e) => setBotName(e.target.value)}
              className="h-10"
            />
          </div>

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

          {/* Language Selection - multi-select, only shown when transcription is enabled */}
          {transcribeEnabled && (
            <div className="space-y-2">
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
                  Auto-detect: the service will detect the language automatically.
                </p>
              )}
            </div>
          )}

          {/* Authenticated Toggle — coming soon */}
          <div className="flex items-center justify-between opacity-50">
            <Label htmlFor="authenticated" className="text-sm flex items-center gap-2 cursor-not-allowed">
              <UserCheck className="h-3.5 w-3.5" />
              Authenticated
              <span className="text-[10px] font-medium bg-muted px-1.5 py-0.5 rounded">Soon</span>
            </Label>
            <Switch
              id="authenticated"
              checked={false}
              disabled
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
            {/* <DocsLink href="/docs/rest/bots#create-bot" /> */}
          </div>
        </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
