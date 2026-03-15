"use client";

import { useMemo, useState, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import { Search, Download, FileText, FileJson, FileVideo, X, Users, MessageSquare, Wifi, WifiOff, Loader2, AlertCircle, Sparkles, Settings, ChevronDown, Mic } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import Image from "next/image";
import { AIChatPanel } from "@/components/ai";
import { getCookie, setCookie } from "@/lib/cookies";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuCheckboxItem,
} from "@/components/ui/dropdown-menu";
import { TranscriptSegment } from "./transcript-segment";
import type { Meeting, TranscriptSegment as TranscriptSegmentType, ChatMessage } from "@/types/vexa";
import { getSpeakerColor } from "@/types/vexa";
import {
  exportToTxt,
  exportToJson,
  exportToSrt,
  exportToVtt,
  downloadFile,
  generateFilename,
} from "@/lib/export";
import { cn } from "@/lib/utils";
import { vexaAPI } from "@/lib/api";
import { toast } from "sonner";
import { LanguagePicker } from "@/components/language-picker";
import { type SegmentGroup } from "@vexaai/transcript-rendering";
import { format } from "date-fns";

// Linkify URLs in chat message text — splits text into plain strings and clickable <a> elements
const URL_REGEX = /(https?:\/\/[^\s<>"')\]]+)/gi;

function linkifyText(text: string, searchQuery?: string): React.ReactNode[] {
  const parts = text.split(URL_REGEX);
  return parts.map((part, i) => {
    if (URL_REGEX.test(part)) {
      // Reset lastIndex since we're using 'g' flag
      URL_REGEX.lastIndex = 0;
      return (
        <a
          key={i}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sky-600 dark:text-sky-400 underline underline-offset-2 hover:text-sky-800 dark:hover:text-sky-300 break-all"
          onClick={(e) => e.stopPropagation()}
        >
          {searchQuery ? highlightChatText(part, searchQuery) : part}
        </a>
      );
    }
    return searchQuery ? highlightChatText(part, searchQuery) : part;
  });
}

function highlightChatText(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const segments = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, "gi"));
  return segments.map((seg, i) =>
    seg.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">
        {seg}
      </mark>
    ) : (
      seg
    )
  );
}

interface TranscriptViewerProps {
  meeting: Meeting;
  segments: TranscriptSegmentType[];
  chatMessages?: ChatMessage[];
  isLoading?: boolean;
  isLive?: boolean;
  // WebSocket connection state (only relevant when isLive=true)
  wsConnecting?: boolean;
  wsConnected?: boolean;
  wsError?: string | null;
  wsReconnectAttempts?: number;
  headerActions?: React.ReactNode;
  topBarContent?: React.ReactNode;
  // Playback sync props
  playbackTime?: number | null;
  /** ISO absolute timestamp of current playback position (for multi-fragment matching) */
  playbackAbsoluteTime?: string | null;
  isPlaybackActive?: boolean;
  onSegmentClick?: (startTimeSeconds: number, absoluteStartTime?: string) => void;
  onTranscribeComplete?: () => void;
}

export function TranscriptViewer({
  meeting,
  segments,
  chatMessages = [],
  isLoading,
  isLive,
  wsConnecting,
  wsConnected,
  wsError,
  wsReconnectAttempts,
  headerActions,
  topBarContent,
  playbackTime,
  playbackAbsoluteTime,
  isPlaybackActive,
  onSegmentClick,
  onTranscribeComplete,
}: TranscriptViewerProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSpeakers, setSelectedSpeakers] = useState<string[]>([]);
  const [transcribeLanguage, setTranscribeLanguage] = useState("auto");
  const [isTranscribing, setIsTranscribing] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false); // Track if user has manually scrolled away from bottom
  const lastScrollTopRef = useRef(0);
  const previousSegmentsLengthRef = useRef(0);

  // ChatGPT prompt state
  const [chatgptPrompt, setChatgptPrompt] = useState(() => {
    if (typeof window !== "undefined") {
      return getCookie("vexa-chatgpt-prompt") || "Read from {url} so I can ask questions about it.";
    }
    return "Read from {url} so I can ask questions about it.";
  });
  const [isChatgptPromptExpanded, setIsChatgptPromptExpanded] = useState(false);
  const [editedChatgptPrompt, setEditedChatgptPrompt] = useState(chatgptPrompt);
  const chatgptPromptTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Measure scroll container for auto-follow
  const isNearBottom = useCallback((el: HTMLElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= 50; // Allow some tolerance
  }, []);
  
  // Track only the most recently updated segment with appended text
  const [mostRecentUpdatedSegment, setMostRecentUpdatedSegment] = useState<{ id: string; appendedText: string } | null>(null);
  const previousSegmentIdsRef = useRef<Set<string>>(new Set());
  const previousSegmentTextsRef = useRef<Map<string, string>>(new Map());
  const highlightTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Keyboard shortcut for search (Cmd/Ctrl + F)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
      if (e.key === "Escape" && document.activeElement === searchInputRef.current) {
        setSearchQuery("");
        searchInputRef.current?.blur();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Split text into sentence chunks for readability (max chars per chunk)
  const splitTextIntoSentenceChunks = useCallback((text: string, maxLen: number): string[] => {
    const normalized = (text || "").trim().replace(/\s+/g, " ");
    if (normalized.length <= maxLen) return [normalized];

    // Split into sentences on punctuation boundaries. Keep punctuation.
    const sentences = normalized.split(/(?<=[.!?])\s+/);
    if (sentences.length === 1) {
      // Single long sentence: return as one chunk to avoid breaking the sentence
      return [normalized];
    }

    const chunks: string[] = [];
    let current = "";
    for (const sentence of sentences) {
      if (current.length === 0) {
        if (sentence.length > maxLen) {
          // Sentence itself exceeds limit: do not split it
          chunks.push(sentence);
        } else {
          current = sentence;
        }
      } else if (current.length + 1 + sentence.length <= maxLen) {
        current = current + " " + sentence;
      } else {
        chunks.push(current);
        if (sentence.length > maxLen) {
          // Sentence exceeds limit: push as is to avoid breaking
          chunks.push(sentence);
          current = "";
        } else {
          current = sentence;
        }
      }
    }
    if (current.length > 0) chunks.push(current);
    return chunks;
  }, []);

  // ---- Timeline item types for unified transcript + chat rendering ----
  type TimelineItem =
    | { type: "transcript"; group: SegmentGroup<TranscriptSegmentType>; index: number }
    | { type: "chat"; message: ChatMessage };

  // Get unique speakers in order of appearance (excluding legacy [Chat] injected segments)
  const speakerOrder = useMemo(() => {
    const speakers: string[] = [];
    for (const segment of segments) {
      if (segment.text?.trimStart().startsWith("[Chat]")) continue;
      if (!speakers.includes(segment.speaker)) {
        speakers.push(segment.speaker);
      }
    }
    return speakers;
  }, [segments]);

  // Raw segments — no grouping, no merging. Each segment rendered individually.
  // Dedup by segment_id when available, otherwise by absolute_start_time.
  const groupedSegments = useMemo(() => {
    const cleaned = segments.filter((seg) => !seg.text?.trimStart().startsWith("[Chat]") && seg.text?.trim());

    // Dedup by segment_id
    const seen = new Map<string, typeof cleaned[0]>();
    for (const seg of cleaned) {
      const key = (seg as any).segment_id || seg.absolute_start_time;
      const existing = seen.get(key);
      if (!existing || (seg.updated_at && existing.updated_at && seg.updated_at > existing.updated_at)) {
        seen.set(key, seg);
      }
    }
    const deduped = Array.from(seen.values()).sort(
      (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
    );

    // Wrap each segment as its own group (1 segment per group)
    return deduped.map((seg): SegmentGroup<typeof seg> => ({
      key: seg.speaker || "",
      startTime: seg.absolute_start_time,
      endTime: seg.absolute_end_time || seg.absolute_start_time,
      startTimeSeconds: seg.start_time ?? 0,
      endTimeSeconds: seg.end_time ?? 0,
      combinedText: (seg.text || "").trim(),
      segments: [seg],
    }));
  }, [segments, segments.length]);

  // Filter grouped segments by search query and selected speakers
  const filteredSegments = useMemo(() => {
    let result = groupedSegments;

    // Filter by selected speakers
    if (selectedSpeakers.length > 0) {
      result = result.filter((g) => selectedSpeakers.includes(g.key));
    }

    // Filter by search query
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (g) =>
          g.combinedText.toLowerCase().includes(query) ||
          g.key.toLowerCase().includes(query)
      );
    }

    return result;
  }, [groupedSegments, searchQuery, selectedSpeakers]);

  // Build unified timeline: merge transcript groups + chat messages, sorted by time
  const timelineItems: TimelineItem[] = useMemo(() => {
    // Start with transcript groups
    const items: TimelineItem[] = filteredSegments.map((group, index) => ({
      type: "transcript" as const,
      group,
      index,
    }));

    // Add chat messages (optionally filtered by search query)
    if (chatMessages.length > 0) {
      const query = searchQuery.trim().toLowerCase();
      for (const msg of chatMessages) {
        if (query && !msg.text.toLowerCase().includes(query) && !msg.sender.toLowerCase().includes(query)) {
          continue;
        }
        items.push({ type: "chat" as const, message: msg });
      }
    }

    // Sort by timestamp: transcript groups use ISO startTime, chat messages use Unix ms
    items.sort((a, b) => {
      const timeA = a.type === "transcript"
        ? new Date(a.group.startTime).getTime()
        : a.message.timestamp;
      const timeB = b.type === "transcript"
        ? new Date(b.group.startTime).getTime()
        : b.message.timestamp;
      return timeA - timeB;
    });

    return items;
  }, [filteredSegments, chatMessages, searchQuery]);

  // Toggle speaker selection
  const toggleSpeaker = useCallback((speaker: string) => {
    setSelectedSpeakers((prev) =>
      prev.includes(speaker)
        ? prev.filter((s) => s !== speaker)
        : [...prev, speaker]
    );
  }, []);

  // Clear all filters
  const clearFilters = useCallback(() => {
    setSearchQuery("");
    setSelectedSpeakers([]);
  }, []);

  const handleTranscribe = useCallback(async () => {
    if (!meeting?.id) return;
    setIsTranscribing(true);
    try {
      const result = await vexaAPI.transcribeMeeting(
        meeting.id,
        transcribeLanguage === "auto" ? undefined : transcribeLanguage
      );
      toast.success(`Transcription complete`, {
        description: `${result.segment_count} segments transcribed`,
      });
      onTranscribeComplete?.();
    } catch (error) {
      toast.error("Transcription failed", {
        description: (error as Error).message,
      });
    } finally {
      setIsTranscribing(false);
    }
  }, [meeting?.id, transcribeLanguage, onTranscribeComplete]);

  const hasActiveFilters = searchQuery.trim() || selectedSpeakers.length > 0;

  // Handle scroll events to detect when user scrolls up
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    const currentScrollTop = el.scrollTop;
    const prevScrollTop = lastScrollTopRef.current;
    lastScrollTopRef.current = currentScrollTop;

    // If user scrolled up, mark them as having scrolled away from bottom
    if (currentScrollTop < prevScrollTop) {
      userScrolledUpRef.current = true;
    }

    // If user is back at bottom, resume auto-scrolling
    if (isNearBottom(el)) {
      userScrolledUpRef.current = false;
    }
  }, [isNearBottom]);

  // Track only the most recently updated segment with appended text
  useEffect(() => {
    if (!isLive || segments.length === 0) {
      previousSegmentIdsRef.current = new Set();
      previousSegmentTextsRef.current.clear();
      return;
    }

    // Create a set of current segment IDs and track text changes
    const currentSegmentIds = new Set<string>();
    const currentSegmentTexts = new Map<string, string>();
    
    segments.forEach((seg) => {
      const id = seg.id || `${seg.absolute_start_time}-${seg.start_time}`;
      currentSegmentIds.add(id);
      currentSegmentTexts.set(id, seg.text || "");
    });

    // Find the most recently updated segment (check from end of array)
    let mostRecentUpdate: { id: string; appendedText: string } | null = null;

    // Check segments from the end (most recent first)
    for (let i = segments.length - 1; i >= 0; i--) {
      const seg = segments[i];
      const id = seg.id || `${seg.absolute_start_time}-${seg.start_time}`;
      const currentText = seg.text || "";
      
      // Check if this is a new segment
      if (!previousSegmentIdsRef.current.has(id)) {
        // For new segments, highlight the entire text initially
        mostRecentUpdate = { id, appendedText: currentText };
        break; // Found the most recent update, stop looking
      } else {
        // Check if text was appended to an existing segment
        const previousText = previousSegmentTextsRef.current.get(id) || "";
        if (currentText.length > previousText.length && currentText.startsWith(previousText)) {
          const appendedText = currentText.slice(previousText.length);
          mostRecentUpdate = { id, appendedText };
          break; // Found the most recent update, stop looking
        }
      }
    }

    // Update state with only the most recent update
    if (mostRecentUpdate) {
      // Clear any existing timeout
      if (highlightTimeoutRef.current) {
        clearTimeout(highlightTimeoutRef.current);
      }

      setMostRecentUpdatedSegment(mostRecentUpdate);

      // Set up timeout to remove highlight after 3 seconds
      highlightTimeoutRef.current = setTimeout(() => {
        setMostRecentUpdatedSegment(null);
        highlightTimeoutRef.current = null;
      }, 3000); // 3 seconds
    }

    // Update previous segment IDs and texts
    previousSegmentIdsRef.current = currentSegmentIds;
    previousSegmentTextsRef.current = currentSegmentTexts;
  }, [segments, isLive]);

  // Cleanup highlight timeout on unmount
  useEffect(() => {
    return () => {
      if (highlightTimeoutRef.current) {
        clearTimeout(highlightTimeoutRef.current);
      }
    };
  }, []);


  // Auto-scroll to bottom when new segments arrive, unless user has scrolled up
  useLayoutEffect(() => {
    if (!isLive) return;

    const el = scrollRef.current;
    if (!el) return;

    // Only auto-scroll when new segments are actually added (not initial load)
    const hasNewSegments = segments.length > previousSegmentsLengthRef.current;
    previousSegmentsLengthRef.current = segments.length;

    if (!hasNewSegments) return;

    // Don't auto-scroll if user has manually scrolled up
    if (userScrolledUpRef.current) return;

    // Only scroll if we're actually near the bottom
    // This prevents scrolling when user is reading older content
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const shouldScroll = distanceFromBottom <= 100; // Allow more tolerance

    if (!shouldScroll) return;

    // Small delay to ensure DOM has updated
    requestAnimationFrame(() => {
      // Double-check element still exists and we're still in live mode
      if (!el || !isLive) return;

      // Scroll to bottom by default (like a chat app)
      bottomRef.current?.scrollIntoView({ block: "end", behavior: "auto" });
    });
  }, [isLive, segments.length]);

  // Find the active segment index during playback.
  // When playbackAbsoluteTime is available (multi-fragment mode), use absolute
  // timestamp comparison. Otherwise fall back to relative time comparison.
  const activePlaybackIndex = useMemo(() => {
    if (!isPlaybackActive) return -1;

    // Absolute time matching (multi-fragment safe)
    if (playbackAbsoluteTime) {
      const pbTime = new Date(playbackAbsoluteTime).getTime();
      for (let i = filteredSegments.length - 1; i >= 0; i--) {
        const group = filteredSegments[i];
        const groupStart = new Date(group.startTime).getTime();
        const groupEnd = new Date(group.endTime).getTime();
        if (groupStart <= pbTime) {
          // Within this group's range (with 1s tolerance)
          if (pbTime <= groupEnd + 1000) return i;
          // Between this group and the next
          if (i < filteredSegments.length - 1) {
            const nextStart = new Date(filteredSegments[i + 1].startTime).getTime();
            if (pbTime < nextStart) return i;
          }
          // Past the last group
          if (i === filteredSegments.length - 1) return i;
          return -1;
        }
      }
      return -1;
    }

    // Fallback: relative time matching (single-fragment)
    if (playbackTime == null) return -1;
    for (let i = filteredSegments.length - 1; i >= 0; i--) {
      const group = filteredSegments[i];
      if (group.startTimeSeconds <= playbackTime) {
        if (playbackTime <= group.endTimeSeconds + 1) return i;
        if (i < filteredSegments.length - 1) {
          const nextGroup = filteredSegments[i + 1];
          if (playbackTime < nextGroup.startTimeSeconds) return i;
        }
        if (i === filteredSegments.length - 1) return i;
        return -1;
      }
    }
    return -1;
  }, [playbackTime, playbackAbsoluteTime, isPlaybackActive, filteredSegments]);

  // Auto-scroll to active playback segment
  const activeSegmentRef = useRef<HTMLDivElement>(null);
  const lastScrolledIndexRef = useRef(-1);

  useEffect(() => {
    if (activePlaybackIndex < 0 || !isPlaybackActive) return;
    // Only scroll when the active segment changes (not on every time update)
    if (activePlaybackIndex === lastScrolledIndexRef.current) return;
    lastScrolledIndexRef.current = activePlaybackIndex;

    // Use a small delay to let the DOM update
    requestAnimationFrame(() => {
      activeSegmentRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
  }, [activePlaybackIndex, isPlaybackActive]);

  // Export handlers
  const handleExport = (format: "txt" | "json" | "srt" | "vtt") => {
    let content: string;
    let mimeType: string;

    switch (format) {
      case "txt":
        content = exportToTxt(meeting, segments);
        mimeType = "text/plain";
        break;
      case "json":
        content = exportToJson(meeting, segments);
        mimeType = "application/json";
        break;
      case "srt":
        content = exportToSrt(segments);
        mimeType = "text/plain";
        break;
      case "vtt":
        content = exportToVtt(segments);
        mimeType = "text/vtt";
        break;
    }

    const filename = generateFilename(meeting, format);
    downloadFile(content, filename, mimeType);
  };

  // Format transcript for ChatGPT
  const formatTranscriptForChatGPT = useCallback(() => {
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
  }, [meeting, segments]);

  // Handle sending transcript to an AI provider (ChatGPT or Perplexity)
  const handleOpenInProvider = useCallback(async (provider: "chatgpt" | "perplexity") => {
    if (segments.length === 0) return;

    // Prefer link-based flow
    try {
      const response = await fetch(
        `/api/vexa/transcripts/${meeting.platform}/${meeting.platform_specific_id}/share?meeting_id=${encodeURIComponent(meeting.id)}`,
        { method: "POST" }
      );
      if (response.ok) {
        const share = (await response.json()) as { url: string; share_id?: string };
        if (share?.url) {
          const publicBase = process.env.NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL?.replace(/\/$/, "");
          const shareUrl =
            publicBase && share.share_id
              ? `${publicBase}/public/transcripts/${share.share_id}.txt`
              : share.url;

          // Use custom prompt, replacing {url} placeholder
          const prompt = chatgptPrompt.replace(/{url}/g, shareUrl);
          
          let providerUrl: string;
          if (provider === "chatgpt") {
            providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(prompt)}`;
          } else {
            providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(prompt)}`;
          }
          
          window.open(providerUrl, "_blank", "noopener,noreferrer");
          return;
        }
      }
    } catch (err) {
      console.error("Failed to create transcript share link:", err);
    }

    // Fallback: clipboard flow
    try {
      const transcriptText = formatTranscriptForChatGPT();
      await navigator.clipboard.writeText(transcriptText);
      const q = "I've copied a meeting transcript to my clipboard. Please wait while I paste it, then I'll ask questions about it.";
      let providerUrl: string;
      if (provider === "chatgpt") {
        providerUrl = `https://chatgpt.com/?hints=search&q=${encodeURIComponent(q)}`;
      } else {
        providerUrl = `https://www.perplexity.ai/search?q=${encodeURIComponent(q)}`;
      }
      setTimeout(() => window.open(providerUrl, "_blank", "noopener,noreferrer"), 100);
    } catch (error) {
      console.error("Failed to copy transcript to clipboard:", error);
    }
  }, [segments, formatTranscriptForChatGPT, meeting.id, meeting.platform, meeting.platform_specific_id, chatgptPrompt]);

  // Handle saving ChatGPT prompt to cookie
  const handleChatgptPromptBlur = useCallback(() => {
    const trimmed = editedChatgptPrompt.trim();
    if (trimmed && trimmed !== chatgptPrompt) {
      setChatgptPrompt(trimmed);
      setCookie("vexa-chatgpt-prompt", trimmed);
    }
  }, [editedChatgptPrompt, chatgptPrompt]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex gap-3">
                <Skeleton className="h-8 w-8 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-4 w-full" />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="flex flex-col h-full flex-1 min-h-0">
      <CardHeader className="flex-shrink-0 space-y-1.5 py-2">
        {/* Thin playback strip (separate row) */}
        {topBarContent && (
          <div className="mb-1">
            {topBarContent}
          </div>
        )}

        {/* Search and Filter Bar */}
        <div className="flex flex-wrap items-center gap-1.5 lg:gap-2">
          {/* Search */}
          <div className="relative flex-1 min-w-[150px] lg:min-w-[200px]">
            <Search className="absolute left-2 lg:left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 lg:h-4 lg:w-4 text-muted-foreground" />
            <Input
              ref={searchInputRef}
              placeholder="Search... (Cmd+F)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className={cn(
                "h-7 lg:h-8 pl-7 lg:pl-9 pr-7 lg:pr-9 text-xs lg:text-sm transition-all",
                searchQuery && "ring-2 ring-primary/20"
              )}
            />
            {searchQuery && (
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-0.5 lg:right-1 top-1/2 -translate-y-1/2 h-6 w-6 lg:h-7 lg:w-7"
                onClick={() => setSearchQuery("")}
              >
                <X className="h-3 w-3 lg:h-4 lg:w-4" />
              </Button>
            )}
          </div>

          {/* Speaker Filter */}
          {speakerOrder.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className={cn(
                    "h-7 lg:h-8 px-2 lg:px-3 text-xs lg:text-sm gap-1 lg:gap-2",
                    selectedSpeakers.length > 0 && "border-primary text-primary"
                  )}
                >
                  <Users className="h-3.5 w-3.5 lg:h-4 lg:w-4" />
                  <span className="hidden sm:inline">Speakers</span>
                  {selectedSpeakers.length > 0 && (
                    <Badge variant="secondary" className="ml-0.5 lg:ml-1 h-4 lg:h-5 px-1 lg:px-1.5 text-[10px] lg:text-xs">
                      {selectedSpeakers.length}
                    </Badge>
                  )}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                {speakerOrder.map((speaker) => {
                  const color = getSpeakerColor(speaker, speakerOrder);
                  return (
                    <DropdownMenuCheckboxItem
                      key={speaker}
                      checked={selectedSpeakers.includes(speaker)}
                      onCheckedChange={() => toggleSpeaker(speaker)}
                    >
                      <div className="flex items-center gap-2">
                        <div className={cn("w-2 h-2 rounded-full", color.avatar)} />
                        <span className="truncate">{speaker || "Unknown"}</span>
                      </div>
                    </DropdownMenuCheckboxItem>
                  );
                })}
                {selectedSpeakers.length > 0 && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setSelectedSpeakers([])}>
                      Clear selection
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {/* Clear all filters */}
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearFilters}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4 mr-1" />
              Clear filters
            </Button>
          )}

          {/* Header actions (e.g., DocsLink) */}
          {headerActions && (
            <div className="ml-auto">
              {headerActions}
            </div>
          )}
        </div>

        {/* Filter results info */}
        {hasActiveFilters && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground animate-fade-in">
            <span>
              Showing {timelineItems.length} of {groupedSegments.length + chatMessages.length} items
              {chatMessages.length > 0 && ` (${chatMessages.length} chat)`}
            </span>
            {searchQuery && (
              <Badge variant="outline" className="font-normal">
                &quot;{searchQuery}&quot;
              </Badge>
            )}
            {selectedSpeakers.map((speaker) => (
              <Badge
                key={speaker}
                variant="secondary"
                className="font-normal cursor-pointer hover:bg-destructive/20"
                onClick={() => toggleSpeaker(speaker)}
              >
                {speaker}
                <X className="h-3 w-3 ml-1" />
              </Badge>
            ))}
          </div>
        )}
      </CardHeader>

      {/* Collapsible ChatGPT Prompt Section */}
      {isChatgptPromptExpanded && (
        <div className="px-6 pb-4 animate-in slide-in-from-top-2 duration-200">
          <div className="bg-muted/30 rounded-lg border p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium flex items-center gap-2">
                <Settings className="h-4 w-4" />
                AI Prompt
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setIsChatgptPromptExpanded(false)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="space-y-2">
              <Input
                ref={chatgptPromptTextareaRef as any}
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
                className="text-sm"
                autoFocus
              />
              <p className="text-[10px] text-muted-foreground">
                Use <code className="px-1 py-0.5 bg-muted rounded">{"{url}"}</code> as a placeholder for the transcript link.
              </p>
            </div>
          </div>
        </div>
      )}

      <CardContent className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 min-h-0 pr-4 overflow-y-auto"
        >
          {timelineItems.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
              {meeting.data?.transcribe_enabled === false && !isLive && !hasActiveFilters ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-muted/50 flex items-center justify-center mb-4">
                    <Mic className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="font-medium mb-1">Recording available</h3>
                  <p className="text-sm text-muted-foreground mb-6">
                    This meeting was recorded without real-time transcription.
                  </p>
                  {isTranscribing ? (
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span className="text-sm">Transcribing recording...</span>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-3">
                      <div className="w-48">
                        <LanguagePicker value={transcribeLanguage} onValueChange={setTranscribeLanguage} />
                      </div>
                      <Button onClick={handleTranscribe}>
                        Transcribe Recording
                      </Button>
                    </div>
                  )}
                </>
              ) : hasActiveFilters ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-muted/50 flex items-center justify-center mb-4">
                    <Search className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="font-medium mb-1">No results found</h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    Try adjusting your search or filters
                  </p>
                  <Button variant="outline" size="sm" onClick={clearFilters}>
                    Clear all filters
                  </Button>
                </>
              ) : isLive && meeting.data?.transcribe_enabled === false ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-red-50 dark:bg-red-950/30 flex items-center justify-center mb-4 relative">
                    <Mic className="h-8 w-8 text-red-500" />
                    <span className="absolute top-1 right-1 h-3 w-3 rounded-full bg-red-500 animate-pulse" />
                  </div>
                  <h3 className="font-medium mb-1">Recording in progress</h3>
                  <p className="text-sm text-muted-foreground">
                    Audio is being captured. Transcription will run after the meeting ends.
                  </p>
                </>
              ) : (
                <>
                  <div className="w-16 h-16 rounded-full bg-muted/50 flex items-center justify-center mb-4">
                    <MessageSquare className="h-8 w-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="font-medium mb-1">No transcript yet</h3>
                  <p className="text-sm text-muted-foreground">
                    {isLive
                      ? "Waiting for speech to transcribe..."
                      : "No transcript available for this meeting"}
                  </p>
                </>
              )}
            </div>
          ) : (
            <div className="space-y-1">
              {timelineItems.map((item, idx) => {
                // ---- Chat message item ----
                if (item.type === "chat") {
                  const msg = item.message;
                  const chatTime = new Date(msg.timestamp);
                  const hh = chatTime.getUTCHours().toString().padStart(2, "0");
                  const mm = chatTime.getUTCMinutes().toString().padStart(2, "0");
                  const ss = chatTime.getUTCSeconds().toString().padStart(2, "0");
                  const displayTime = `${hh}:${mm}:${ss}`;

                  return (
                    <div
                      key={`chat-${msg.timestamp}-${idx}`}
                      className="animate-fade-in flex gap-3 p-3 rounded-lg bg-sky-50/60 dark:bg-sky-950/20 border border-sky-200/50 dark:border-sky-800/30"
                    >
                      {/* Chat icon instead of avatar */}
                      <div className="h-8 w-8 flex-shrink-0 rounded-full bg-sky-500 flex items-center justify-center">
                        <MessageSquare className="h-4 w-4 text-white" />
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-medium text-sm text-sky-700 dark:text-sky-400">
                            {msg.sender}
                          </span>
                          <Badge variant="outline" className="text-[10px] h-4 px-1.5 border-sky-300 dark:border-sky-700 text-sky-600 dark:text-sky-400">
                            chat
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {displayTime}
                          </span>
                        </div>
                        <p className="text-sm leading-relaxed">
                          {linkifyText(msg.text, searchQuery || undefined)}
                        </p>
                      </div>
                    </div>
                  );
                }

                // ---- Transcript group item ----
                const { group, index } = item;

                // Create a synthetic segment for the grouped segment
                const syntheticSegment: TranscriptSegmentType = {
                  id: `${group.startTime}-${index}`,
                  meeting_id: meeting.id,
                  start_time: group.startTimeSeconds,
                  end_time: group.endTimeSeconds,
                  absolute_start_time: group.startTime,
                  absolute_end_time: group.endTime,
                  text: group.combinedText,
                  speaker: group.key.startsWith("__unmapped_") ? (group.segments[0]?.speaker || "") : group.key,
                  language: group.segments[0]?.language || "en",
                  session_uid: group.segments[0]?.session_uid || "",
                  created_at: group.startTime,
                };

                // Check if this group contains the most recently updated segment
                let textToHighlight: string | null = null;

                if (mostRecentUpdatedSegment) {
                  const matchingSegment = group.segments.find((seg) => {
                    const id = seg.id || `${seg.absolute_start_time}-${seg.start_time}`;
                    return mostRecentUpdatedSegment.id === id;
                  });

                  if (matchingSegment) {
                    const isLastSegmentInGroup =
                      group.segments[group.segments.length - 1]?.id === matchingSegment.id ||
                      (group.segments[group.segments.length - 1]?.absolute_start_time === matchingSegment.absolute_start_time &&
                       group.segments[group.segments.length - 1]?.start_time === matchingSegment.start_time);

                    if (isLastSegmentInGroup && group.combinedText.endsWith(mostRecentUpdatedSegment.appendedText)) {
                      textToHighlight = mostRecentUpdatedSegment.appendedText;
                    } else {
                      const segmentIndex = group.segments.indexOf(matchingSegment);
                      if (segmentIndex >= 0) {
                        let textBeforeThisSegment = "";
                        for (let i = 0; i < segmentIndex; i++) {
                          textBeforeThisSegment += (group.segments[i].text || "").trim() + " ";
                        }
                        const segmentStartInCombined = textBeforeThisSegment.length;
                        const segmentEndInCombined = segmentStartInCombined + (matchingSegment.text || "").trim().length;
                        const segmentTextInCombined = group.combinedText.slice(segmentStartInCombined, segmentEndInCombined);
                        if (segmentTextInCombined.endsWith(mostRecentUpdatedSegment.appendedText)) {
                          textToHighlight = mostRecentUpdatedSegment.appendedText;
                        }
                      }
                    }
                  }
                }

                const isActivePlayback = activePlaybackIndex === index;

                // Determine if this is a continuation from the same speaker
                // by looking at the previous timeline item
                let showSpeakerHeader = true;
                if (idx > 0) {
                  const prevItem = timelineItems[idx - 1];
                  if (prevItem.type === "transcript") {
                    const prevSpeaker = prevItem.group.key.startsWith("__unmapped_")
                      ? (prevItem.group.segments[0]?.speaker || "")
                      : prevItem.group.key;
                    const currSpeaker = syntheticSegment.speaker;
                    if (prevSpeaker === currSpeaker) {
                      showSpeakerHeader = false;
                    }
                  }
                }

                return (
                  <div
                    key={`${group.startTime}-${index}`}
                    ref={isActivePlayback ? activeSegmentRef : undefined}
                    className={cn("animate-fade-in", showSpeakerHeader && idx > 0 && "mt-1")}
                    style={{
                      animationDelay: isLive ? "0ms" : `${Math.min(index * 20, 200)}ms`,
                      animationFillMode: "backwards",
                    }}
                  >
                    <TranscriptSegment
                      segment={syntheticSegment}
                      speakerColor={getSpeakerColor(group.key, speakerOrder)}
                      searchQuery={searchQuery}
                      isHighlighted={searchQuery.length > 0}
                      appendedText={textToHighlight}
                      isActivePlayback={isActivePlayback}
                      onClickSegment={onSegmentClick ? () => onSegmentClick(group.startTimeSeconds, group.startTime) : undefined}
                      showSpeakerHeader={showSpeakerHeader}
                    />
                  </div>
                );
              })}
            </div>
          )}
          {/* Bottom sentinel for reliable auto-follow scrolling */}
          <div ref={bottomRef} />
        </div>
      </CardContent>
    </Card>
  );
}
