"use client";

import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Play } from "lucide-react";
import type { TranscriptSegment as TranscriptSegmentType, SpeakerColor } from "@/types/vexa";

interface TranscriptSegmentProps {
  segment: TranscriptSegmentType;
  speakerColor: SpeakerColor;
  isHighlighted?: boolean;
  searchQuery?: string;
  appendedText?: string | null;
  isActivePlayback?: boolean;
  onClickSegment?: () => void;
}

function formatTimestamp(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function formatAbsoluteTimestamp(utcAbsoluteTime: string): string {
  try {
    // Extract time directly from ISO 8601 string (e.g., "2025-11-27T15:12:22.341578+00:00" -> "15:12:22")
    // This avoids any device-dependent timezone conversion
    // Format: YYYY-MM-DDTHH:MM:SS... or YYYY-MM-DDTHH:MM:SSZ
    const timeMatch = utcAbsoluteTime.match(/T(\d{2}):(\d{2})(?::(\d{2}))?/);
    if (timeMatch) {
      const hh = timeMatch[1];
      const mm = timeMatch[2];
      const ss = timeMatch[3] ?? "00";
      return `${hh}:${mm}:${ss}`;
    }
    // Fallback if format doesn't match expected pattern
    return "00:00:00";
  } catch (error) {
    // Fallback to relative timestamp if absolute time is invalid
    console.error("Error parsing absolute timestamp:", error);
    return "00:00:00";
  }
}

function getInitials(name: string | null | undefined): string {
  if (!name) return "??";
  return name
    .split(" ")
    .map((n) => n[0])
    .filter(Boolean)
    .join("")
    .toUpperCase()
    .slice(0, 2) || "??";
}

function highlightText(text: string, query: string): React.ReactNode {
  if (!query) return text;

  const parts = text.split(new RegExp(`(${query})`, "gi"));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

function renderTextWithAppendedHighlight(
  text: string,
  appendedText: string | null,
  searchQuery?: string
): React.ReactNode {
  if (!appendedText) {
    return searchQuery ? highlightText(text, searchQuery) : text;
  }

  // Only highlight if the appended text is at the end of the text
  // This ensures we don't highlight random occurrences in the middle
  if (!text.endsWith(appendedText)) {
    // If appended text is not at the end, don't highlight anything
    return searchQuery ? highlightText(text, searchQuery) : text;
  }

  // The appended text is at the end, highlight only that portion
  const beforeText = text.slice(0, text.length - appendedText.length);
  const highlightedText = text.slice(text.length - appendedText.length);

  return (
    <>
      {searchQuery ? highlightText(beforeText, searchQuery) : beforeText}
      <mark className="bg-blue-200 dark:bg-blue-200 dark:text-black rounded px-0.5 animate-highlight-fade">
        {searchQuery ? highlightText(highlightedText, searchQuery) : highlightedText}
      </mark>
    </>
  );
}

export function TranscriptSegment({
  segment,
  speakerColor,
  isHighlighted,
  searchQuery,
  appendedText,
  isActivePlayback,
  onClickSegment,
}: TranscriptSegmentProps) {
  // Always display absolute time from the feed when available (device-independent).
  // For grouped segments, callers should pass the FIRST segment's `absolute_start_time` as `segment.absolute_start_time`.
  const displayTimestamp = segment.absolute_start_time
    ? formatAbsoluteTimestamp(segment.absolute_start_time)
    : formatTimestamp(segment.start_time);

  return (
    <div
      onClick={onClickSegment}
      className={cn(
        "group flex gap-3 p-3 rounded-lg transition-colors",
        isHighlighted && "bg-yellow-50 dark:bg-yellow-900/20",
        isActivePlayback && "bg-primary/10 border-l-2 border-primary",
        !isHighlighted && !isActivePlayback && "hover:bg-muted/50",
        onClickSegment && "cursor-pointer"
      )}
    >
      {/* Avatar */}
      <Avatar className={cn("h-8 w-8 flex-shrink-0", speakerColor.avatar)}>
        <AvatarFallback className={cn("text-xs font-medium text-white", speakerColor.avatar)}>
          {getInitials(segment.speaker)}
        </AvatarFallback>
      </Avatar>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className={cn("font-medium text-sm", speakerColor.text)}>
            {segment.speaker || "Unknown Speaker"}
          </span>
          <span className="text-xs text-muted-foreground">
            {displayTimestamp}
          </span>
          {onClickSegment && (
            <span
              className={cn(
                "ml-auto inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-all",
                isActivePlayback
                  ? "opacity-100 border-primary/40 bg-primary/10 text-primary"
                  : "opacity-80 group-hover:opacity-100 group-hover:border-primary/40 group-hover:bg-primary/10 group-hover:text-primary"
              )}
              aria-label="Click segment to play from this timestamp"
              title="Click to play from this segment"
            >
              <Play className="h-3 w-3" />
              Play
            </span>
          )}
        </div>
        <p className="text-sm leading-relaxed">
          {renderTextWithAppendedHighlight(segment.text, appendedText || null, searchQuery)}
        </p>
      </div>
    </div>
  );
}
