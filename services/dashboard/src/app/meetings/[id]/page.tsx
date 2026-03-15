"use client";

import { useEffect, useState, useRef, useCallback, useMemo, type CSSProperties } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { format } from "date-fns";
import {
  ArrowLeft,
  Calendar,
  Clock,
  Users,
  Globe,
  Video,
  Pencil,
  Check,
  X,
  Sparkles,
  Loader2,
  FileText,
  StopCircle,
  FileJson,
  FileVideo,
  ChevronDown,
  Settings,
  ExternalLink,
  Trash2,
  Zap,
  Code,
  Download,
  ClipboardCopy,
  Share,
} from "lucide-react";
import { AudioPlayer, type AudioPlayerHandle, type AudioFragment } from "@/components/recording/audio-player";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ErrorState } from "@/components/ui/error-state";
import { TranscriptViewer } from "@/components/transcript/transcript-viewer";
import { BotStatusIndicator, BotFailedIndicator } from "@/components/meetings/bot-status-indicator";
import { WsEventLog, RestTranscriptsPreview, RestRecordingsPreview } from "@/components/meetings/ws-event-log";
// ChatPanel removed — chat messages now render inline in TranscriptViewer
import { AIChatPanel } from "@/components/ai";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useAuthStore } from "@/stores/auth-store";
import { useLiveTranscripts } from "@/hooks/use-live-transcripts";
import { PLATFORM_CONFIG, getDetailedStatus } from "@/types/vexa";
import type { MeetingStatus, Meeting } from "@/types/vexa";
import { StatusHistory } from "@/components/meetings/status-history";
import { cn } from "@/lib/utils";
import { vexaAPI } from "@/lib/api";
import { toast } from "sonner";
import { LanguagePicker } from "@/components/language-picker";
import { WHISPER_LANGUAGE_CODES, getLanguageDisplayName } from "@/lib/languages";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  exportToTxt,
  exportToJson,
  exportToSrt,
  exportToVtt,
  downloadFile,
  generateFilename,
} from "@/lib/export";
import { getCookie, setCookie } from "@/lib/cookies";
import { DocsLink } from "@/components/docs/docs-link";
import { DecisionsPanel } from "@/components/decisions/decisions-panel";
import { WebhookDeliverySection } from "@/components/webhooks/webhook-delivery-section";

const API_BASE_URL = process.env.NEXT_PUBLIC_VEXA_PUBLIC_API_URL || "https://api.vexa.ai";

export default function MeetingDetailPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const idParam = (params as { id?: string | string[] } | null)?.id;
  const meetingId = Array.isArray(idParam) ? idParam[0] : (idParam ?? "");

  const {
    currentMeeting,
    transcripts,
    recordings,
    chatMessages,
    isLoadingMeeting,
    isLoadingTranscripts,
    isUpdatingMeeting,
    error,
    fetchMeeting,
    refreshMeeting,
    fetchTranscripts,
    fetchChatMessages,
    updateMeetingStatus,
    updateMeetingData,
    deleteMeeting,
    clearCurrentMeeting,
  } = useMeetingsStore();
  const authToken = useAuthStore((s) => s.token);

  // Decisions panel state
  const [decisionsOpen, setDecisionsOpen] = useState(false);

  // API view toggle state — default ON when coming from onboarding (?apiView=1)
  const [apiViewOpen, setApiViewOpen] = useState(() => searchParams?.get("apiView") === "1");
  const [apiButtonHighlight, setApiButtonHighlight] = useState(false);
  const apiButtonRef = useRef<HTMLButtonElement>(null);

  // Title editing state
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);

  // Notes editing state
  const [isEditingNotes, setIsEditingNotes] = useState(false);
  const [editedNotes, setEditedNotes] = useState("");
  const [isSavingNotes, setIsSavingNotes] = useState(false);
  const [isNotesExpanded, setIsNotesExpanded] = useState(false);
  const notesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldSetCursorToEnd = useRef(false);

  // ChatGPT prompt editing state
  const [chatgptPrompt, setChatgptPrompt] = useState(() => {
    if (typeof window !== "undefined") {
      return getCookie("vexa-chatgpt-prompt") || "Read from {url} so I can ask questions about it.";
    }
    return "Read from {url} so I can ask questions about it.";
  });
  const [isChatgptPromptExpanded, setIsChatgptPromptExpanded] = useState(false);
  const [editedChatgptPrompt, setEditedChatgptPrompt] = useState(chatgptPrompt);
  const chatgptPromptTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Bot control state
  const [isStoppingBot, setIsStoppingBot] = useState(false);
  const [isDeletingMeeting, setIsDeletingMeeting] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [forcePostMeetingMode, setForcePostMeetingMode] = useState(false);
  
  // Bot config state
  const [currentLanguage, setCurrentLanguage] = useState<string | undefined>(
    currentMeeting?.data?.languages?.[0] || "auto"
  );
  const [isUpdatingConfig, setIsUpdatingConfig] = useState(false);

  // Audio playback state
  const audioPlayerRef = useRef<AudioPlayerHandle>(null);
  const [playbackTime, setPlaybackTime] = useState<number | null>(null);
  const [isPlaybackActive, setIsPlaybackActive] = useState(false);
  const [pendingSeekTime, setPendingSeekTime] = useState<number | null>(null);
  const [activeFragmentIndex, setActiveFragmentIndex] = useState(0);

  // Build ordered recording fragments for multi-fragment playback.
  // Each recording has a session_uid, created_at, and media_files with duration.
  // Sort by created_at so fragments play sequentially.
  const recordingFragments = useMemo((): AudioFragment[] => {
    // Include recordings that have audio media files, whether completed or in_progress
    // (in_progress recordings may have snapshot uploads available for playback)
    const availableRecordings = recordings
      .filter(r => (r.status === "completed" || r.status === "in_progress") && r.media_files?.some(mf => mf.type === "audio"))
      .sort((a, b) => a.created_at.localeCompare(b.created_at));

    return availableRecordings.map(rec => {
      const audioMedia = rec.media_files.find(mf => mf.type === "audio")!;
      return {
        src: vexaAPI.getRecordingAudioUrl(rec.id, audioMedia.id),
        duration: audioMedia.duration_seconds || 0,
        sessionUid: rec.session_uid,
        createdAt: rec.created_at,
      };
    });
  }, [recordings]);

  const hasRecordingAudio = recordingFragments.length > 0;

  // Derive each session's start time (wall-clock ms) from segment data.
  // segment.start_time is relative to session start, and segment.absolute_start_time
  // is wall-clock UTC. So: sessionStart = absolute_start_time - start_time.
  // We compute one per session_uid to support multi-fragment meetings.
  const sessionStartMsBySessionUid = useMemo((): Map<string, number> => {
    const map = new Map<string, number>();
    for (const seg of transcripts) {
      if (!seg.absolute_start_time || seg.start_time == null) continue;
      const uid = seg.session_uid || "";
      if (map.has(uid)) continue; // use the first segment per session
      const absMs = new Date(seg.absolute_start_time).getTime();
      const sessionMs = absMs - seg.start_time * 1000;
      map.set(uid, sessionMs);
    }
    return map;
  }, [transcripts]);

  const handlePlaybackTimeUpdate = useCallback((time: number) => {
    setPlaybackTime(time);
    setIsPlaybackActive(true);
  }, []);

  const handleFragmentChange = useCallback((index: number) => {
    setActiveFragmentIndex(index);
  }, []);

  // Map a segment click to the correct recording fragment and seek position.
  //
  // segment.start_time is relative to session start — the same reference point
  // as the audio recording (both start from session start). So start_time IS
  // the correct seek position within the recording fragment.
  //
  // For multi-fragment: use absolute_start_time + session_uid to find the right fragment,
  // then use start_time as the seek offset within that fragment.
  //
  // KNOWN ISSUE: Audio playback seek is off by a few seconds when clicking segments.
  // segment.start_time is relative to SegmentPublisher session start, but the recording
  // (MediaRecorder) starts slightly later. resetSessionStart() in recording.ts reduces
  // the gap but doesn't eliminate it — there's still a few-second delta between when
  // the session start is reset and when MediaRecorder.start() actually fires inside
  // page.evaluate(). A precise fix would require the browser to signal the exact
  // MediaRecorder start timestamp back to Node.js.
  const handleSegmentClick = useCallback((startTimeSeconds: number, absoluteStartTime?: string) => {
    if (!hasRecordingAudio) {
      setPendingSeekTime(startTimeSeconds);
      return;
    }

    if (recordingFragments.length <= 1) {
      // Single recording — start_time is the seek position
      audioPlayerRef.current?.seekTo(startTimeSeconds);
      setPlaybackTime(startTimeSeconds);
      setIsPlaybackActive(true);
      return;
    }

    // Multi-fragment: find which fragment this segment belongs to
    let targetFragmentIndex = 0;
    if (absoluteStartTime) {
      const segTimeMs = new Date(absoluteStartTime).getTime();
      const matchingSegment = transcripts.find(
        s => s.absolute_start_time === absoluteStartTime
      );
      if (matchingSegment?.session_uid) {
        const uidIndex = recordingFragments.findIndex(
          f => f.sessionUid === matchingSegment.session_uid
        );
        if (uidIndex >= 0) targetFragmentIndex = uidIndex;
      } else {
        // Fallback: find fragment by derived session start
        for (let i = recordingFragments.length - 1; i >= 0; i--) {
          const uid = recordingFragments[i].sessionUid;
          const sessionStart = sessionStartMsBySessionUid.get(uid);
          if (sessionStart != null && sessionStart <= segTimeMs) {
            targetFragmentIndex = i;
            break;
          }
        }
      }
    }

    audioPlayerRef.current?.seekToFragment(targetFragmentIndex, startTimeSeconds);
    const virtualOffset = recordingFragments
      .slice(0, targetFragmentIndex)
      .reduce((sum, f) => sum + (f.duration || 0), 0);
    setPlaybackTime(virtualOffset + startTimeSeconds);
    setIsPlaybackActive(true);
  }, [hasRecordingAudio, recordingFragments, transcripts, sessionStartMsBySessionUid]);

  useEffect(() => {
    if (!hasRecordingAudio || pendingSeekTime == null) return;
    const timer = setTimeout(() => {
      audioPlayerRef.current?.seekTo(pendingSeekTime);
      setPlaybackTime(pendingSeekTime);
      setIsPlaybackActive(true);
      setPendingSeekTime(null);
    }, 0);
    return () => clearTimeout(timer);
  }, [hasRecordingAudio, pendingSeekTime]);

  // Track if initial load is complete to prevent animation replays
  const hasLoadedRef = useRef(false);

  // Handle meeting status change from WebSocket
  const handleStatusChange = useCallback((status: MeetingStatus) => {
    // Refetch when status changes so we get latest data and post-meeting artifacts.
    if (status === "active" || status === "stopping" || status === "completed" || status === "failed") {
      fetchMeeting(meetingId);
    }
    if (
      (status === "stopping" || status === "completed") &&
      currentMeeting?.platform &&
      currentMeeting?.platform_specific_id
    ) {
      fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id));
    }
  }, [fetchMeeting, fetchTranscripts, meetingId, currentMeeting?.platform, currentMeeting?.platform_specific_id, currentMeeting?.id]);

  // Handle stopping the bot
  const handleStopBot = useCallback(async () => {
    if (!currentMeeting) return;
    setIsStoppingBot(true);
    try {
      await vexaAPI.stopBot(currentMeeting.platform, currentMeeting.platform_specific_id);
      // Optimistic transition to post-meeting UI immediately after stop is accepted.
      setForcePostMeetingMode(true);
      updateMeetingStatus(String(currentMeeting.id), "stopping");
      fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id));
      toast.success("Bot stopped", {
        description: "The transcription has been stopped.",
      });
      fetchMeeting(meetingId);
    } catch (error) {
      toast.error("Failed to stop bot", {
        description: (error as Error).message,
      });
    } finally {
      setIsStoppingBot(false);
    }
  }, [currentMeeting, fetchMeeting, fetchTranscripts, meetingId, updateMeetingStatus]);

  // Handle language change
  const handleLanguageChange = useCallback(async (newLanguage: string) => {
    if (!currentMeeting) return;
    setIsUpdatingConfig(true);
    try {
      await vexaAPI.updateBotConfig(currentMeeting.platform, currentMeeting.platform_specific_id, {
        language: newLanguage === "auto" ? undefined : newLanguage,
        task: "transcribe", // Always use transcribe mode
      });
      setCurrentLanguage(newLanguage);
      updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
        languages: [newLanguage],
      });
      toast.success("Language updated successfully");
    } catch (error) {
      toast.error("Failed to update language", {
        description: (error as Error).message,
      });
    } finally {
      setIsUpdatingConfig(false);
    }
  }, [currentMeeting, updateMeetingData]);

  const handleDeleteMeeting = useCallback(async () => {
    if (!currentMeeting) return;
    setIsDeletingMeeting(true);
    try {
      await deleteMeeting(
        currentMeeting.platform,
        currentMeeting.platform_specific_id,
        currentMeeting.id
      );
      toast.success("Meeting deleted");
      router.push("/meetings");
    } catch (error) {
      toast.error("Failed to delete meeting", {
        description: (error as Error).message,
      });
    } finally {
      setIsDeletingMeeting(false);
    }
  }, [currentMeeting, deleteMeeting, router]);

  // Handle export
  const handleExport = useCallback((format: "txt" | "json" | "srt" | "vtt") => {
    if (!currentMeeting) {
      toast.error("No meeting selected");
      return;
    }
    if (transcripts.length === 0) {
      toast.info("No transcript available yet", {
        description: "The transcript will be available once the meeting starts and transcription begins.",
      });
      return;
    }
    
    let content: string;
    let mimeType: string;

    switch (format) {
      case "txt":
        content = exportToTxt(currentMeeting, transcripts);
        mimeType = "text/plain";
        break;
      case "json":
        content = exportToJson(currentMeeting, transcripts);
        mimeType = "application/json";
        break;
      case "srt":
        content = exportToSrt(transcripts);
        mimeType = "text/plain";
        break;
      case "vtt":
        content = exportToVtt(transcripts);
        mimeType = "text/vtt";
        break;
    }

    const filename = generateFilename(currentMeeting, format);
    downloadFile(content, filename, mimeType);
  }, [currentMeeting, transcripts]);

  // Format transcript for ChatGPT
  const formatTranscriptForChatGPT = useCallback((meeting: Meeting, segments: typeof transcripts): string => {
    let output = "Meeting Transcript\n\n";
    
    if (meeting.data?.name || meeting.data?.title) {
      output += `Title: ${meeting.data?.name || meeting.data?.title}\n`;
    }
    
    if (meeting.start_time) {
      output += `Date: ${format(new Date(meeting.start_time), "PPPp")}\n`;
    }
    
    if (meeting.data?.participants?.length) {
      output += `Participants: ${meeting.data.participants.join(", ")}\n`;
    }
    
    output += "\n---\n\n";
    
    for (const segment of segments) {
      // Use absolute timestamp if available
      let timestamp = "";
      if (segment.absolute_start_time) {
        try {
          const date = new Date(segment.absolute_start_time);
          timestamp = date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "").replace("Z", "");
        } catch {
          timestamp = segment.absolute_start_time;
        }
      } else if (segment.start_time !== undefined) {
        // Fallback to relative timestamp
        const minutes = Math.floor(segment.start_time / 60);
        const seconds = Math.floor(segment.start_time % 60);
        timestamp = `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
      }
      
      if (timestamp) {
        output += `[${timestamp}] ${segment.speaker}: ${segment.text}\n\n`;
      } else {
        output += `${segment.speaker}: ${segment.text}\n\n`;
      }
    }
    
    return output;
  }, []);

  // Handle opening transcript in AI provider
  const handleOpenInProvider = useCallback(async (provider: "chatgpt" | "perplexity") => {
    if (!currentMeeting) {
      toast.error("No meeting selected");
      return;
    }
    if (transcripts.length === 0) {
      toast.info("No transcript available yet", {
        description: "The transcript will be available once the meeting starts and transcription begins.",
      });
      return;
    }

    // Prefer link-based flow (like "Read from https://..." in ChatGPT/Perplexity)
    try {
      const share = await vexaAPI.createTranscriptShare(
        currentMeeting.platform,
        currentMeeting.platform_specific_id,
        meetingId
      );

      // If the gateway is accessed via localhost (dev), providers still need a PUBLIC URL.
      // Allow overriding the public base via NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL.
      const publicBase = process.env.NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL?.replace(/\/$/, "");
      const shareUrl =
        publicBase && share.share_id
          ? `${publicBase}/public/transcripts/${share.share_id}.txt`
          : share.url;

      // Use custom prompt from cookie, replacing {url} placeholder
      const prompt = chatgptPrompt.replace(/{url}/g, shareUrl);
      
      let providerUrl: string;
      if (provider === "chatgpt") {
        providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(prompt)}`;
      } else {
        // Perplexity format: https://www.perplexity.ai/search?q={query}
        providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(prompt)}`;
      }
      
      window.open(providerUrl, "_blank", "noopener,noreferrer");
      return;
    } catch (err) {
      // Fall back to clipboard flow if share-link creation fails
      console.error("Failed to create transcript share link:", err);
    }

    try {
      const transcriptText = formatTranscriptForChatGPT(currentMeeting, transcripts);
      await navigator.clipboard.writeText(transcriptText);
      toast.success("Transcript copied to clipboard", {
        description: `Opening ${provider === "chatgpt" ? "ChatGPT" : "Perplexity"}. Please paste the transcript when prompted.`,
      });
      const q = "I've copied a meeting transcript to my clipboard. Please wait while I paste it, then I'll ask questions about it.";
      let providerUrl: string;
      if (provider === "chatgpt") {
        providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(q)}`;
      } else {
        providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(q)}`;
      }
      setTimeout(() => window.open(providerUrl, "_blank", "noopener,noreferrer"), 100);
    } catch (error) {
      toast.error("Failed to copy transcript", {
        description: "Please try again or copy the transcript manually.",
      });
    }
  }, [currentMeeting, transcripts, formatTranscriptForChatGPT, meetingId, chatgptPrompt]);

  // Handle sending transcript to ChatGPT (for main button)
  const handleSendToChatGPT = useCallback(() => {
    handleOpenInProvider("chatgpt");
  }, [handleOpenInProvider]);

  // Handle saving ChatGPT prompt to cookie
  const handleChatgptPromptBlur = useCallback(() => {
    const trimmed = editedChatgptPrompt.trim();
    if (trimmed && trimmed !== chatgptPrompt) {
      setChatgptPrompt(trimmed);
      setCookie("vexa-chatgpt-prompt", trimmed);
    }
  }, [editedChatgptPrompt, chatgptPrompt]);

  // Live transcripts and status updates via WebSocket (for active and early states)
  const isEarlyState =
    currentMeeting?.status === "requested" ||
    currentMeeting?.status === "joining" ||
    currentMeeting?.status === "awaiting_admission";
  const isStoppingState = currentMeeting?.status === "stopping";
  const shouldUseWebSocket =
    currentMeeting?.status === "active" || isEarlyState || isStoppingState;
  
  const {
    isConnecting: wsConnecting,
    isConnected: wsConnected,
    connectionError: wsError,
    reconnectAttempts,
  } = useLiveTranscripts({
    platform: currentMeeting?.platform ?? "google_meet",
    nativeId: currentMeeting?.platform_specific_id ?? "",
    meetingId: meetingId,
    isActive: shouldUseWebSocket,
    onStatusChange: handleStatusChange,
  });

  useEffect(() => {
    if (meetingId) {
      setForcePostMeetingMode(false);
      fetchMeeting(meetingId);
    }

    return () => {
      clearCurrentMeeting();
      hasLoadedRef.current = false;
    };
  }, [meetingId, fetchMeeting, clearCurrentMeeting]);

  // Mark as loaded once we have data
  useEffect(() => {
    if (currentMeeting && !hasLoadedRef.current) {
      hasLoadedRef.current = true;
    }
  }, [currentMeeting]);

  // Show detected language from backend first (meeting.data.languages or from segments), then user can change via toggle
  const validLangCodes = useMemo(
    () => new Set(WHISPER_LANGUAGE_CODES),
    []
  );
  useEffect(() => {
    if (!currentMeeting) return;
    const fromData = currentMeeting.data?.languages?.[0];
    if (fromData && fromData !== "auto") {
      setCurrentLanguage(fromData);
      return;
    }
    // When not set by backend, use first detected language from segments (backend returns it per segment)
    const fromSegment = transcripts.find(
      (t) => t.language && t.language !== "unknown" && validLangCodes.has(t.language)
    )?.language;
    setCurrentLanguage(fromSegment || "auto");
  }, [currentMeeting, transcripts, validLangCodes]);

  // No longer need polling - WebSocket handles status updates for early states
  // Removed auto-refresh polling since WebSocket provides real-time updates

  // Fetch transcripts when meeting is loaded
  // Use specific properties as dependencies to avoid unnecessary refetches
  const meetingPlatform = currentMeeting?.platform;
  const meetingNativeId = currentMeeting?.platform_specific_id;
  const meetingNumericId = currentMeeting?.id ? String(currentMeeting.id) : undefined;
  const meetingStatus = currentMeeting?.status;

  useEffect(() => {
    // Always refresh transcript/recording artifacts when entering post-meeting flow.
    if ((meetingStatus === "stopping" || meetingStatus === "completed") && meetingPlatform && meetingNativeId) {
      fetchTranscripts(meetingPlatform, meetingNativeId, meetingNumericId);
      fetchChatMessages(meetingPlatform, meetingNativeId);
      return;
    }

    // During non-WS states, use REST fetch as source of truth.
    if (!shouldUseWebSocket && meetingPlatform && meetingNativeId) {
      fetchTranscripts(meetingPlatform, meetingNativeId, meetingNumericId);
      fetchChatMessages(meetingPlatform, meetingNativeId);
    }
  }, [meetingStatus, shouldUseWebSocket, meetingPlatform, meetingNativeId, meetingNumericId, fetchTranscripts, fetchChatMessages]);

  // Also fetch chat messages for active meetings (WS handles real-time, REST bootstraps)
  useEffect(() => {
    if (shouldUseWebSocket && meetingPlatform && meetingNativeId) {
      fetchChatMessages(meetingPlatform, meetingNativeId);
    }
  }, [shouldUseWebSocket, meetingPlatform, meetingNativeId, fetchChatMessages]);

  // Handle saving notes on blur
  const handleNotesBlur = useCallback(async () => {
    if (!currentMeeting || isSavingNotes) return;

    const originalNotes = currentMeeting.data?.notes || "";
    const trimmedNotes = editedNotes.trim();

    // Only save if content has changed
    if (trimmedNotes === originalNotes) {
      setIsEditingNotes(false);
      return;
    }

    setIsSavingNotes(true);
    try {
      await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
        notes: trimmedNotes,
      });
      setIsEditingNotes(false);
    } catch (err) {
      toast.error("Failed to save notes");
      // Keep in edit mode on error so user can retry
    } finally {
      setIsSavingNotes(false);
    }
  }, [currentMeeting, editedNotes, isSavingNotes, updateMeetingData]);

  // Handle setting cursor to end when textarea is focused
  const handleNotesFocus = useCallback((e: React.FocusEvent<HTMLTextAreaElement>) => {
    if (shouldSetCursorToEnd.current && editedNotes) {
      const textarea = e.currentTarget;
      const length = editedNotes.length;
      // Use setTimeout to ensure the textarea is fully rendered
      setTimeout(() => {
        textarea.setSelectionRange(length, length);
      }, 0);
      shouldSetCursorToEnd.current = false;
    }
  }, [editedNotes]);

  // Compute absolute playback time for transcript highlight matching.
  // Convert the playback position to an absolute (wall-clock) ISO timestamp
  // so the transcript viewer can match against segment absolute_start_time.
  //
  // Key insight: segment.start_time is relative to the session start (when
  // SegmentPublisher was constructed), and the audio file also starts recording
  // around the same time. So playbackTime (seconds from audio start) roughly
  // equals seconds from session start. We derive the session start wall-clock
  // time from the segments: sessionStart = absolute_start_time - start_time.
  //
  // Previously this used recording.created_at, which is the upload time — not
  // when the recording actually started — causing a large offset.
  // Convert playback position (seconds from session start) to absolute wall-clock
  // time so the transcript viewer can highlight the matching segment.
  const playbackAbsoluteTime = useMemo((): string | null => {
    if (playbackTime == null || !isPlaybackActive || recordingFragments.length === 0) return null;
    if (recordingFragments.length === 1) {
      const uid = recordingFragments[0].sessionUid;
      const sessionStart = sessionStartMsBySessionUid.get(uid);
      if (sessionStart == null) return null;
      return new Date(sessionStart + playbackTime * 1000).toISOString();
    }
    // Multi-fragment: find which fragment the virtual time falls in
    let remaining = playbackTime;
    for (let i = 0; i < recordingFragments.length; i++) {
      const fragDur = recordingFragments[i].duration || 0;
      if (remaining <= fragDur || i === recordingFragments.length - 1) {
        const uid = recordingFragments[i].sessionUid;
        const sessionStart = sessionStartMsBySessionUid.get(uid);
        if (sessionStart == null) return null;
        return new Date(sessionStart + remaining * 1000).toISOString();
      }
      remaining -= fragDur;
    }
    return null;
  }, [playbackTime, isPlaybackActive, recordingFragments, sessionStartMsBySessionUid]);

  if (error) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" onClick={() => router.back()}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back
        </Button>
        <ErrorState
          error={error}
          onRetry={() => fetchMeeting(meetingId)}
        />
      </div>
    );
  }

  if (isLoadingMeeting || !currentMeeting) {
    return <MeetingDetailSkeleton />;
  }

  const platformConfig = PLATFORM_CONFIG[currentMeeting.platform];
  const statusConfig = getDetailedStatus(currentMeeting.status, currentMeeting.data);

  // Safety check: ensure statusConfig is always defined
  if (!statusConfig) {
    console.error("statusConfig is undefined for status:", currentMeeting.status);
    return <MeetingDetailSkeleton />;
  }

  const duration =
    currentMeeting.start_time && currentMeeting.end_time
      ? Math.round(
          (new Date(currentMeeting.end_time).getTime() -
            new Date(currentMeeting.start_time).getTime()) /
            60000
        )
      : null;
  const isPostMeetingFlow =
    forcePostMeetingMode ||
    currentMeeting.status === "stopping" || currentMeeting.status === "completed";
  const hasRecordingEntries = recordings.length > 0;
  const noAudioRecordingForMeeting =
    (currentMeeting.data?.recording_enabled === false && !hasRecordingAudio) ||
    (currentMeeting.status === "completed" && !hasRecordingEntries);
  const canUseSegmentPlayback = isPostMeetingFlow && !noAudioRecordingForMeeting;
  const recordingTopBar = isPostMeetingFlow ? (
    hasRecordingAudio ? (
      <AudioPlayer
        ref={audioPlayerRef}
        fragments={recordingFragments}
        onTimeUpdate={handlePlaybackTimeUpdate}
        onFragmentChange={handleFragmentChange}
        compact
      />
    ) : noAudioRecordingForMeeting ? (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        No audio recording for this meeting.
      </div>
    ) : (
      <div className="flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-lg border text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Recording is processing...
      </div>
    )
  ) : null;

  const formatDuration = (minutes: number) => {
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  };

  return (
    <div className="space-y-2 lg:space-y-6 h-full flex flex-col">
      {/* Desktop Header */}
      <div className="hidden lg:flex items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <Button variant="ghost" size="sm" asChild className="-ml-2 h-8 px-2 text-muted-foreground hover:text-foreground">
            <Link href="/meetings">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          
          {isEditingTitle ? (
            <div className="flex items-center gap-2 flex-1 max-w-md">
              <div className="flex items-center gap-2 flex-1">
                <Input
                  value={editedTitle}
                  onChange={(e) => setEditedTitle(e.target.value)}
                  className="text-xl font-bold h-9"
                  placeholder="Meeting title..."
                  autoFocus
                  disabled={isSavingTitle}
                onKeyDown={async (e) => {
                  if (e.key === "Enter" && editedTitle.trim()) {
                    setIsSavingTitle(true);
                    try {
                      await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                        name: editedTitle.trim(),
                      });
                      setIsEditingTitle(false);
                      toast.success("Title updated");
                    } catch (err) {
                      toast.error("Failed to update title");
                    } finally {
                      setIsSavingTitle(false);
                    }
                  } else if (e.key === "Escape") {
                    setIsEditingTitle(false);
                  }
                }}
              />
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-green-600"
                  disabled={isSavingTitle || !editedTitle.trim()}
                  onClick={async () => {
                    if (!editedTitle.trim()) return;
                    setIsSavingTitle(true);
                    try {
                      await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                        name: editedTitle.trim(),
                      });
                      setIsEditingTitle(false);
                      toast.success("Title updated");
                    } catch (err) {
                      toast.error("Failed to update title");
                    } finally {
                      setIsSavingTitle(false);
                    }
                  }}
                >
                  {isSavingTitle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-muted-foreground"
                  disabled={isSavingTitle}
                  onClick={() => setIsEditingTitle(false)}
                >
                  <X className="h-4 w-4" />
                </Button>
                <DocsLink href="/docs/cookbook/rename-meeting" />
              </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex items-center gap-2 group min-w-0">
                <h1 className="text-xl font-bold tracking-tight truncate">
                  {currentMeeting.data?.name || currentMeeting.data?.title || currentMeeting.platform_specific_id}
                </h1>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                  onClick={() => {
                    setEditedTitle(currentMeeting.data?.name || currentMeeting.data?.title || "");
                    setIsEditingTitle(true);
                  }}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
              </div>
              <Badge className={cn("shrink-0", statusConfig.bgColor, statusConfig.color)}>
                {statusConfig.label}
              </Badge>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {(currentMeeting.status === "active" || currentMeeting.status === "completed") && transcripts.length > 0 && (
            <div className="flex items-center gap-2">
              <AIChatPanel
                meeting={currentMeeting}
                transcripts={transcripts}
                trigger={
                  <Button className="gap-2 h-9">
                    <Sparkles className="h-4 w-4" />
                    Ask AI
                  </Button>
                }
              />
              
              <div className="flex items-center gap-2">
                <DropdownMenu>
                  <div className="flex items-center border rounded-md overflow-hidden bg-background shadow-sm h-9">
                    <Button
                      variant="ghost"
                      className="gap-2 rounded-r-none border-r-0 hover:bg-muted h-full"
                      onClick={() => handleExport("txt")}
                      title="Export"
                    >
                      <Share className="h-4 w-4" />
                      <span>Export</span>
                    </Button>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="w-9 rounded-l-none border-l hover:bg-muted h-full"
                      >
                        <ChevronDown className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                  </div>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => handleOpenInProvider("chatgpt")}>
                    <Image src="/icons/icons8-chatgpt-100.png" alt="ChatGPT" width={16} height={16} className="object-contain mr-2 invert dark:invert-0" />
                    Open in ChatGPT
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleOpenInProvider("perplexity")}>
                    <Image src="/icons/icons8-perplexity-ai-100.png" alt="Perplexity" width={16} height={16} className="object-contain mr-2" />
                    Open in Perplexity
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem asChild>
                    <Link href="/docs/cookbook/share-transcript-url" target="_blank" rel="noopener noreferrer" className="flex items-center">
                      <ExternalLink className="h-4 w-4 mr-2" />
                      API Docs: Share URL
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => {
                      if (!isChatgptPromptExpanded) {
                        setEditedChatgptPrompt(chatgptPrompt);
                        setIsChatgptPromptExpanded(true);
                      } else {
                        setIsChatgptPromptExpanded(false);
                      }
                    }}
                  >
                    <Settings className="h-4 w-4 mr-2" />
                    Configure Prompt
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => handleExport("txt")}>
                    <FileText className="h-4 w-4 mr-2" />
                    Download .txt
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleExport("json")}>
                    <FileJson className="h-4 w-4 mr-2" />
                    Download .json
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      if (!currentMeeting) return;
                      const content = JSON.stringify(currentMeeting, null, 2);
                      const filename = `metadata-${currentMeeting.platform_specific_id}.json`;
                      downloadFile(content, filename, "application/json");
                    }}
                  >
                    <Code className="h-4 w-4 mr-2" />
                    Download metadata
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => {
                      if (!currentMeeting || transcripts.length === 0) return;
                      const text = exportToTxt(currentMeeting, transcripts);
                      navigator.clipboard.writeText(text).then(() => {
                        toast.success("Transcript copied to clipboard");
                      });
                    }}
                    disabled={transcripts.length === 0}
                  >
                    <ClipboardCopy className="h-4 w-4 mr-2" />
                    Copy to clipboard
                  </DropdownMenuItem>
                  {hasRecordingAudio && (
                    <DropdownMenuItem
                      onClick={() => {
                        if (recordingFragments.length > 0) {
                          const link = document.createElement("a");
                          link.href = recordingFragments[0].src;
                          link.download = `${currentMeeting?.data?.name || currentMeeting?.data?.title || "recording"}.webm`;
                          link.click();
                        }
                      }}
                    >
                      <Download className="h-4 w-4 mr-2" />
                      Download audio
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              <DocsLink href="/docs/cookbook/share-transcript-url" />
              </div>
            </div>
          )}
          {currentMeeting.status === "active" && (
            <div className="flex items-center">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="outline"
                    className="gap-2 text-destructive hover:text-destructive hover:bg-destructive/10 h-9"
                    disabled={isStoppingBot}
                  >
                    {isStoppingBot ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <StopCircle className="h-4 w-4" />
                    )}
                    Stop
                  </Button>
                </AlertDialogTrigger>
              <AlertDialogContent className={apiViewOpen ? "sm:max-w-lg" : undefined}>
                <AlertDialogHeader>
                  <AlertDialogTitle>Stop Transcription?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will disconnect the bot from the meeting and stop the live transcription. You can still access the transcript after stopping.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                {apiViewOpen && currentMeeting && (
                  <div className="rounded-lg overflow-hidden border border-border bg-[#111111] font-mono text-[11px]">
                    <div className="px-3 py-2 bg-[#1a1a1a] flex items-center justify-between">
                      <div className="flex items-center gap-[5px]">
                        <span className="w-2 h-2 rounded-full bg-[#ff5f57]" />
                        <span className="w-2 h-2 rounded-full bg-[#febc2e]" />
                        <span className="w-2 h-2 rounded-full bg-[#28c840]" />
                      </div>
                      <span className="text-[10px] text-gray-500">DELETE /bots</span>
                    </div>
                    <div className="p-3 leading-relaxed">
                      <div className="text-gray-500 mb-2"># Stop the bot</div>
                      <div>
                        <span className="text-gray-300">curl -X </span>
                        <span className="text-[#fca5a5]">DELETE</span>
                        <span className="text-gray-300"> \</span>
                      </div>
                      <div className="pl-4">
                        <span className="text-[#6ee7b7]">{API_BASE_URL}/bots/{currentMeeting.platform}/{currentMeeting.platform_specific_id}</span>
                        <span className="text-gray-300"> \</span>
                      </div>
                      <div className="pl-4">
                        <span className="text-gray-300">-H </span>
                        <span className="text-[#7dd3fc]">&apos;X-API-Key: {authToken ? `${authToken.slice(0, 8)}...` : "vx_sk_..."}&apos;</span>
                      </div>
                    </div>
                  </div>
                )}
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleStopBot}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    Stop Transcription
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <DocsLink href="/docs/rest/bots#stop-bot" />
            </div>
          )}

          {/* API view toggle */}
          <Button
            ref={apiButtonRef}
            variant={apiViewOpen ? "secondary" : "outline"}
            size="sm"
            className={cn(
              "gap-1.5 h-9 transition-all duration-300",
              apiButtonHighlight && "ring-2 ring-gray-950 dark:ring-white ring-offset-2 ring-offset-background animate-pulse"
            )}
            onClick={() => {
              setApiViewOpen((v) => !v);
              setApiButtonHighlight(false);
            }}
          >
            <Code className="h-4 w-4 text-emerald-500" />
            <span className="hidden sm:inline">API</span>
          </Button>

          {/* Decisions panel toggle */}
          {process.env.NEXT_PUBLIC_TRACKER_ENABLED === "true" && (
          <Button
            variant={decisionsOpen ? "secondary" : "outline"}
            size="sm"
            className="gap-1.5 h-9"
            onClick={() => setDecisionsOpen((v) => !v)}
          >
            <Zap className="h-4 w-4 text-amber-500" />
            <span className="hidden sm:inline">Decisions</span>
          </Button>
          )}
        </div>
      </div>

      {/* API Tutorial Mode Banner */}
      {apiViewOpen && (
        <div className="hidden lg:flex items-center justify-between gap-3 mb-4 px-5 py-3 rounded-xl bg-gray-950 dark:bg-white">
          <div className="flex items-center gap-3">
            <span className="w-[7px] h-[7px] rounded-full bg-emerald-400 animate-pulse shrink-0" />
            <span className="text-[13px] font-medium text-white dark:text-gray-950">
              API Tutorial Mode
            </span>
            <span className="text-[13px] text-gray-400 dark:text-gray-500">
              Showing live API calls & WebSocket events
            </span>
          </div>
          <button
            className="text-gray-400 hover:text-white dark:hover:text-gray-950 transition-colors p-1"
            onClick={() => {
              setApiViewOpen(false);
              setApiButtonHighlight(true);
              setTimeout(() => setApiButtonHighlight(false), 3000);
            }}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Participants List - Desktop Only */}
      {currentMeeting.data?.participants && currentMeeting.data.participants.length > 0 && (
        <div className="hidden lg:block mb-6">
          <p className="text-sm text-muted-foreground">
            With {currentMeeting.data.participants.slice(0, 4).join(", ")}
            {currentMeeting.data.participants.length > 4 && ` +${currentMeeting.data.participants.length - 4} more`}
          </p>
        </div>
      )}

      {/* Mobile: Single consolidated block with everything */}
      <div className="lg:hidden sticky top-[-16px] z-40 bg-background/80 backdrop-blur-sm -mx-4 px-4 py-2 mb-2">
        <div
          className={cn(
            "bg-card text-card-foreground rounded-lg border shadow-sm px-2 py-1.5",
            "backdrop-blur supports-[backdrop-filter]:bg-card/95"
          )}
        >
          {/* Single Highly Compact Row for Mobile */}
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="h-7 w-7 -ml-0.5 shrink-0" asChild>
              <Link href="/meetings">
                <ArrowLeft className="h-3.5 w-3.5" />
              </Link>
            </Button>

            {/* Title & Platform Icon */}
            <div className="flex-1 min-w-0 flex items-center gap-1">
              {isEditingTitle ? (
                <div className="flex items-center gap-1 flex-1 min-w-0">
                  <Input
                    value={editedTitle}
                    onChange={(e) => setEditedTitle(e.target.value)}
                    className="text-[11px] font-medium h-6 flex-1 min-w-0 py-0 px-1.5"
                    placeholder="Title..."
                    autoFocus
                    disabled={isSavingTitle}
                    onBlur={() => {
                      if (!isSavingTitle) setIsEditingTitle(false);
                    }}
                    onKeyDown={async (e) => {
                      if (e.key === "Enter" && editedTitle.trim()) {
                        setIsSavingTitle(true);
                        try {
                          await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                            name: editedTitle.trim(),
                          });
                          setIsEditingTitle(false);
                          toast.success("Title updated");
                        } catch (err) {
                          toast.error("Failed to update title");
                        } finally {
                          setIsSavingTitle(false);
                        }
                      } else if (e.key === "Escape") {
                        setIsEditingTitle(false);
                      }
                    }}
                  />
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6 text-green-600 shrink-0"
                    disabled={isSavingTitle || !editedTitle.trim()}
                    onClick={async () => {
                      if (!editedTitle.trim()) return;
                      setIsSavingTitle(true);
                      try {
                        await updateMeetingData(currentMeeting.platform, currentMeeting.platform_specific_id, {
                          name: editedTitle.trim(),
                        });
                        setIsEditingTitle(false);
                        toast.success("Title updated");
                      } catch (err) {
                        toast.error("Failed to update title");
                      } finally {
                        setIsSavingTitle(false);
                      }
                    }}
                  >
                    {isSavingTitle ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                  </Button>
                  <DocsLink href="/docs/cookbook/rename-meeting" />
                </div>
              ) : (
                <div 
                  className="flex items-center gap-1 group cursor-pointer min-w-0"
                  onClick={() => {
                    setEditedTitle(currentMeeting.data?.name || currentMeeting.data?.title || "");
                    setIsEditingTitle(true);
                  }}
                >
                  <span className="text-xs font-semibold truncate">
                    {currentMeeting.data?.name || currentMeeting.data?.title || currentMeeting.platform_specific_id}
                  </span>
                  <Pencil className="h-3 w-3 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground" />
                </div>
              )}
            </div>

            {/* Status & Actions */}
            <div className="flex items-center gap-1 shrink-0">
              <Badge className={cn("text-[9px] h-4 px-1 shrink-0", statusConfig.bgColor, statusConfig.color)}>
                {statusConfig.label}
              </Badge>

              {/* Language Selector - Mobile (only when active) */}
              {currentMeeting.status === "active" && (
                <div className="flex items-center gap-0.5 shrink-0 ml-0.5">
                  <LanguagePicker
                    value={currentLanguage ?? "auto"}
                    onValueChange={handleLanguageChange}
                    disabled={isUpdatingConfig}
                    compact
                  />
                  {isUpdatingConfig && (
                    <Loader2 className="h-2.5 w-2.5 animate-spin" />
                  )}
                </div>
              )}

              <div className="flex items-center border-l ml-0.5 pl-0.5 gap-0">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground"
                  onClick={() => {
                    setEditedNotes(currentMeeting.data?.notes || "");
                    setIsEditingNotes(true);
                    setIsNotesExpanded(true);
                  }}
                  title="Notes"
                >
                  <FileText className="h-3.5 w-3.5" />
                </Button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon" className="h-7 w-7 ml-0.5" title="Export">
                      <Share className="h-3.5 w-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => handleOpenInProvider("chatgpt")} disabled={transcripts.length === 0}>
                      <Image src="/icons/icons8-chatgpt-100.png" alt="ChatGPT" width={16} height={16} className="object-contain mr-2 invert dark:invert-0" />
                      Open in ChatGPT
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleOpenInProvider("perplexity")} disabled={transcripts.length === 0}>
                      <Image src="/icons/icons8-perplexity-ai-100.png" alt="Perplexity" width={16} height={16} className="object-contain mr-2" />
                      Open in Perplexity
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        if (!isChatgptPromptExpanded) {
                          setEditedChatgptPrompt(chatgptPrompt);
                          setIsChatgptPromptExpanded(true);
                        } else {
                          setIsChatgptPromptExpanded(false);
                        }
                      }}
                    >
                      <Settings className="h-4 w-4 mr-2" />
                      Configure Prompt
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem asChild>
                      <Link href="/docs/cookbook/share-transcript-url" target="_blank" rel="noopener noreferrer" className="flex items-center">
                        <ExternalLink className="h-4 w-4 mr-2" />
                        API Docs: Share URL
                      </Link>
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => handleExport("txt")} disabled={transcripts.length === 0}>
                      <FileText className="h-4 w-4 mr-2" />
                      Download .txt
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleExport("json")} disabled={transcripts.length === 0}>
                      <FileJson className="h-4 w-4 mr-2" />
                      Download .json
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        if (!currentMeeting) return;
                        const content = JSON.stringify(currentMeeting, null, 2);
                        const filename = `metadata-${currentMeeting.platform_specific_id}.json`;
                        downloadFile(content, filename, "application/json");
                      }}
                    >
                      <Code className="h-4 w-4 mr-2" />
                      Download metadata
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        if (!currentMeeting || transcripts.length === 0) return;
                        const text = exportToTxt(currentMeeting, transcripts);
                        navigator.clipboard.writeText(text).then(() => {
                          toast.success("Transcript copied to clipboard");
                        });
                      }}
                      disabled={transcripts.length === 0}
                    >
                      <ClipboardCopy className="h-4 w-4 mr-2" />
                      Copy to clipboard
                    </DropdownMenuItem>
                    {hasRecordingAudio && (
                      <DropdownMenuItem
                        onClick={() => {
                          if (recordingFragments.length > 0) {
                            const link = document.createElement("a");
                            link.href = recordingFragments[0].src;
                            link.download = `${currentMeeting?.data?.name || currentMeeting?.data?.title || "recording"}.webm`;
                            link.click();
                          }
                        }}
                      >
                        <Download className="h-4 w-4 mr-2" />
                        Download audio
                      </DropdownMenuItem>
                    )}
                </DropdownMenuContent>
              </DropdownMenu>
              <DocsLink href="/docs/cookbook/share-transcript-url" />

                {currentMeeting.status === "active" && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive ml-0.5"
                        disabled={isStoppingBot}
                        title="Stop"
                      >
                        {isStoppingBot ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <StopCircle className="h-4 w-4" />
                        )}
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Stop Transcription?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will disconnect the bot and stop transcribing.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={handleStopBot}
                          className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                          Stop
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>


      {/* Collapsible Notes Section - Mobile Only */}
      {isNotesExpanded && (
        <div className="lg:hidden sticky top-0 z-50 bg-card text-card-foreground rounded-lg border shadow-sm overflow-hidden animate-in slide-in-from-top-2 duration-200">
          <div className="p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Notes</span>
              <div className="flex items-center gap-2">
                {isSavingNotes && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Saving...
                  </div>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0"
                  onClick={() => {
                    setIsNotesExpanded(false);
                    setIsEditingNotes(false);
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <Textarea
              ref={notesTextareaRef}
              value={editedNotes}
              onChange={(e) => setEditedNotes(e.target.value)}
              onFocus={handleNotesFocus}
              onBlur={handleNotesBlur}
              placeholder="Add notes about this meeting..."
              className="min-h-[120px] resize-none text-sm"
              disabled={isSavingNotes}
              autoFocus
            />
          </div>
        </div>
      )}

      {/* Collapsible AI Prompt Section - Mobile Only */}
      {isChatgptPromptExpanded && (
        <div className="lg:hidden sticky top-0 z-50 bg-card text-card-foreground rounded-lg border shadow-sm overflow-hidden animate-in slide-in-from-top-2 duration-200">
          <div className="p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">AI Prompt</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => {
                  setIsChatgptPromptExpanded(false);
                }}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="space-y-2">
              <Textarea
                ref={chatgptPromptTextareaRef}
                value={editedChatgptPrompt}
                onChange={(e) => setEditedChatgptPrompt(e.target.value)}
                onBlur={handleChatgptPromptBlur}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setEditedChatgptPrompt(chatgptPrompt);
                    setIsChatgptPromptExpanded(false);
                  }
                }}
                placeholder="AI prompt (use {url} for the transcript URL)"
                className="min-h-[120px] resize-none text-sm"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                Use <code className="px-1 py-0.5 bg-muted rounded">{"{url}"}</code> as a placeholder for the transcript URL.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        {/* Transcript or Status Indicator */}
        <div className="lg:col-span-2 order-2 lg:order-1 flex flex-col min-h-0 flex-1">
          {/* Show bot status for early states */}
          {(currentMeeting.status === "requested" ||
            currentMeeting.status === "joining" ||
            currentMeeting.status === "awaiting_admission") && (
            <BotStatusIndicator
              status={currentMeeting.status}
              platform={currentMeeting.platform}
              meetingId={currentMeeting.platform_specific_id}
              createdAt={currentMeeting.created_at}
              updatedAt={currentMeeting.updated_at}
              transcribeEnabled={currentMeeting.data?.transcribe_enabled !== false}
              onStopped={() => {
                fetchMeeting(meetingId);
              }}
            />
          )}

          {/* Show failed indicator */}
          {currentMeeting.status === "failed" && (
            <BotFailedIndicator
              status={currentMeeting.status}
              errorMessage={currentMeeting.data?.error || currentMeeting.data?.failure_reason || currentMeeting.data?.status_message}
              errorCode={currentMeeting.data?.error_code}
            />
          )}

          {/* Keep transcript visible through stopping -> completed transition */}
          {(currentMeeting.status === "active" ||
            currentMeeting.status === "stopping" ||
            currentMeeting.status === "completed") && (
            <TranscriptViewer
              meeting={currentMeeting}
              segments={transcripts}
              chatMessages={chatMessages}
              isLoading={isLoadingTranscripts}
              isLive={currentMeeting.status === "active"}
              wsConnecting={wsConnecting}
              wsConnected={wsConnected}
              wsError={wsError}
              wsReconnectAttempts={reconnectAttempts}
              headerActions={<DocsLink href="/docs/cookbook/get-transcripts" />}
              topBarContent={recordingTopBar}
              playbackTime={playbackTime}
              playbackAbsoluteTime={playbackAbsoluteTime}
              isPlaybackActive={isPlaybackActive}
              onSegmentClick={canUseSegmentPlayback ? handleSegmentClick : undefined}
              onTranscribeComplete={() => {
                fetchMeeting(meetingId);
                if (currentMeeting?.platform && currentMeeting?.platform_specific_id) {
                  fetchTranscripts(currentMeeting.platform, currentMeeting.platform_specific_id, String(currentMeeting.id));
                }
              }}
            />
          )}

        </div>

        {/* Sidebar - sticky on desktop, hidden on mobile */}
        <div className="hidden lg:block order-1 lg:order-2">
          <div className="lg:sticky lg:top-6 space-y-6">
          {apiViewOpen ? (
            <>
            <WsEventLog
              status={currentMeeting.status}
              platform={currentMeeting.platform}
              nativeId={currentMeeting.platform_specific_id}
              wsConnected={wsConnected}
              wsConnecting={wsConnecting}
              segmentCount={transcripts.length}
            />
            <RestTranscriptsPreview
              platform={currentMeeting.platform}
              nativeId={currentMeeting.platform_specific_id}
              segmentCount={transcripts.length}
              token={authToken}
            />
            <RestRecordingsPreview
              platform={currentMeeting.platform}
              nativeId={currentMeeting.platform_specific_id}
              token={authToken}
            />
            </>
          ) : (
          <>
          {/* Meeting Info */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Video className="h-4 w-4" />
                Meeting Info
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Platform & Meeting ID */}
              <div className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-lg flex items-center justify-center overflow-hidden bg-background">
                  <Image
                    src={currentMeeting.platform === "google_meet"
                      ? "/icons/icons8-google-meet-96.png"
                      : currentMeeting.platform === "teams"
                      ? "/icons/icons8-teams-96.png"
                      : "/icons/icons8-zoom-96.png"}
                    alt={platformConfig.name}
                    width={32}
                    height={32}
                    className="object-contain"
                  />
                </div>
                <div>
                  <p className="text-sm font-medium">{platformConfig.name}</p>
                  <p className="text-sm text-muted-foreground font-mono">
                    {currentMeeting.platform_specific_id}
                  </p>
                </div>
              </div>

              {/* Date */}
              {currentMeeting.start_time && (
                <div className="flex items-center gap-3">
                  <Calendar className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">Date</p>
                    <p className="text-sm text-muted-foreground">
                      {format(new Date(currentMeeting.start_time), "PPPp")}
                    </p>
                  </div>
                </div>
              )}

              {/* Duration */}
              {duration && (
                <div className="flex items-center gap-3">
                  <Clock className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">Duration</p>
                    <p className="text-sm text-muted-foreground">
                      {formatDuration(duration)}
                    </p>
                  </div>
                </div>
              )}

              {/* Bot Settings - Only show when active */}
              {currentMeeting.status === "active" && (
                <>
                  <Separator />
                  <div className="space-y-3">
                    <p className="text-sm font-medium">Bot Settings</p>
                    
                    {/* Language Selection - shows detected language from backend first, user can change */}
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-muted-foreground">Language</label>
                        <DocsLink href="/docs/rest/bots#update-bot-configuration" />
                      </div>
                      <p className="text-xs text-muted-foreground">
                        When not set, the service detects the language automatically. You can change it below if needed.
                      </p>
                      <div className="flex items-center gap-2">
                        <LanguagePicker
                          value={currentLanguage ?? "auto"}
                          onValueChange={handleLanguageChange}
                          disabled={isUpdatingConfig}
                          triggerClassName="h-9 w-full justify-between"
                        />
                        {isUpdatingConfig && (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        )}
                      </div>
                    </div>

                    {isUpdatingConfig && (
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        <span>Updating...</span>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Languages (read-only when not active) */}
              {currentMeeting.status !== "active" &&
                currentMeeting.data?.languages &&
                currentMeeting.data.languages.length > 0 && (
                  <div className="flex items-center gap-3">
                    <Globe className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium">Languages</p>
                      <p className="text-sm text-muted-foreground">
                        {currentMeeting.data.languages.map(getLanguageDisplayName).join(", ")}
                      </p>
                    </div>
                  </div>
                )}
            </CardContent>
          </Card>

          {/* Participants */}
          {currentMeeting.data?.participants &&
            currentMeeting.data.participants.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Users className="h-4 w-4" />
                    Participants ({currentMeeting.data.participants.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {currentMeeting.data.participants.map((participant, index) => (
                      <div
                        key={index}
                        className="flex items-center gap-2 text-sm group"
                      >
                        <div className="h-2 w-2 rounded-full bg-primary transition-transform group-hover:scale-125" />
                        <span className="group-hover:text-primary transition-colors">{participant}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

          {/* Details */}
          <Card>
            <CardHeader>
              <CardTitle>Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Status with description */}
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Status</span>
                <div className="text-right">
                  <span className={cn("font-medium", statusConfig.color)}>
                    {statusConfig.label}
                  </span>
                  {statusConfig.description && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {statusConfig.description}
                    </p>
                  )}
                </div>
              </div>
              <Separator />
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Speakers</span>
                <span className="font-medium">
                  {new Set(transcripts.map((t) => t.speaker)).size}
                </span>
              </div>
              <Separator />
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Words</span>
                <span className="font-medium">
                  {transcripts.reduce(
                    (acc, t) => acc + t.text.split(/\s+/).length,
                    0
                  )}
                </span>
              </div>

              {/* Status History */}
              {currentMeeting.data?.status_transition && currentMeeting.data.status_transition.length > 0 && (
                <>
                  <Separator />
                  <StatusHistory transitions={currentMeeting.data.status_transition} />
                </>
              )}
            </CardContent>
          </Card>

          {/* Notes */}
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CardTitle className="flex items-center gap-2">
                    <FileText className="h-4 w-4" />
                    Notes
                  </CardTitle>
                  <DocsLink href="/docs/rest/meetings#update-meeting-data" />
                </div>
                {isSavingNotes && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Saving...
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {isEditingNotes ? (
                <Textarea
                  ref={notesTextareaRef}
                  value={editedNotes}
                  onChange={(e) => setEditedNotes(e.target.value)}
                  onFocus={handleNotesFocus}
                  onBlur={handleNotesBlur}
                  placeholder="Add notes about this meeting..."
                  className="min-h-[120px] resize-none"
                  disabled={isSavingNotes}
                  autoFocus
                />
              ) : currentMeeting.data?.notes ? (
                <p
                  className="text-sm text-muted-foreground whitespace-pre-wrap cursor-text hover:bg-muted/50 rounded-md p-2 -m-2 transition-colors"
                  onClick={() => {
                    setEditedNotes(currentMeeting.data?.notes || "");
                    shouldSetCursorToEnd.current = true;
                    setIsEditingNotes(true);
                  }}
                >
                  {currentMeeting.data.notes}
                </p>
              ) : (
                <div
                  className="text-sm text-muted-foreground italic cursor-text hover:bg-muted/50 rounded-md p-2 -m-2 transition-colors min-h-[120px] flex items-center"
                  onClick={() => {
                    setEditedNotes("");
                    shouldSetCursorToEnd.current = false;
                    setIsEditingNotes(true);
                  }}
                >
                  Click here to add notes...
                </div>
              )}
            </CardContent>
          </Card>

          {(currentMeeting.status === "completed" || currentMeeting.status === "failed") && (
            <Card className="border-destructive/30">
              <CardContent className="pt-6">
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="destructive"
                      className="w-full gap-2"
                      disabled={isDeletingMeeting}
                      onClick={() => setDeleteConfirmText("")}
                    >
                      {isDeletingMeeting ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                      Delete meeting
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete meeting?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This removes transcript data and anonymizes meeting data. Type <strong>delete</strong> to confirm.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <div className="py-2">
                      <Input
                        placeholder='Type "delete" to confirm'
                        value={deleteConfirmText}
                        onChange={(e) => setDeleteConfirmText(e.target.value)}
                        autoFocus
                      />
                    </div>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={handleDeleteMeeting}
                        disabled={deleteConfirmText.trim().toLowerCase() !== "delete" || isDeletingMeeting}
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        Delete meeting
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </CardContent>
            </Card>
          )}
          </>
          )}
          </div>
        </div>
      </div>

      {/* Webhook Delivery Section */}
      {currentMeeting.status === "completed" && (
        <div className="mt-6">
          <WebhookDeliverySection meetingId={meetingId} />
        </div>
      )}

      {/* Decisions slide-over panel */}
      {/* Backdrop */}
      {decisionsOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm lg:hidden"
          onClick={() => setDecisionsOpen(false)}
        />
      )}
      {/* Panel */}
      <div
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex flex-col w-full sm:w-[420px]",
          "bg-background border-l shadow-2xl",
          "transform transition-transform duration-300 ease-in-out",
          decisionsOpen ? "translate-x-0" : "translate-x-full"
        )}
      >
        {/* Panel header */}
        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-amber-500" />
            <span className="font-semibold text-sm">Decisions</span>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => setDecisionsOpen(false)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        {/* Panel content — scrollable */}
        <div className="flex-1 overflow-y-auto p-4">
          <DecisionsPanel
            meetingId={meetingId}
            isActive={currentMeeting.status === "active"}
            embedded
          />
        </div>
      </div>
    </div>
  );
}

function MeetingDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-40" />
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <div className="flex gap-2">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-20" />
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Skeleton className="h-[600px]" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-48" />
          <Skeleton className="h-40" />
        </div>
      </div>
    </div>
  );
}
