"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { vexaAPI } from "@/lib/api";
import type { RecordingFrame, RecordingFrameListResponse } from "@/types/vexa";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ImageIcon, Loader2, RefreshCw, AlertCircle } from "lucide-react";

// --- Helper: format seconds to mm:ss or h:mm:ss ---

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h >= 1) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// --- Props ---

interface SnapshotGalleryProps {
  recordingId: number;
  /** Callback when a snapshot timestamp is clicked. Receives timestamp in seconds. */
  onTimestampClick?: (timestampSeconds: number) => void;
  /** Whether the meeting has a video recording (affects empty state messaging). */
  hasVideo?: boolean;
  /** Additional CSS classes for the container. */
  className?: string;
}

// --- Component ---

export function SnapshotGallery({
  recordingId,
  onTimestampClick,
  hasVideo = true,
  className,
}: SnapshotGalleryProps) {
  const [frames, setFrames] = useState<RecordingFrame[]>([]);
  const [extractionStatus, setExtractionStatus] = useState<RecordingFrameListResponse["extraction_status"] | null>(null);
  const [total, setTotal] = useState(0);
  const [failureReason, setFailureReason] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const gridRef = useRef<HTMLDivElement>(null);

  // Fetch frames on mount
  useEffect(() => {
    let cancelled = false;

    async function fetchFrames() {
      try {
        const data = await vexaAPI.getRecordingFrames(recordingId);
        if (cancelled) return;
        setExtractionStatus(data.extraction_status);
        setTotal(data.total);
        setFrames(data.frames);
        setFailureReason(data.failure_reason ?? null);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        // 404 means snapshots disabled — return null (hide gallery)
        if (err instanceof Error && "status" in err && (err as { status: number }).status === 404) {
          setExtractionStatus("none");
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load snapshots");
      }
    }

    fetchFrames();
    return () => { cancelled = true; };
  }, [recordingId]);

  // Poll while processing
  useEffect(() => {
    if (extractionStatus !== "processing") return;

    const interval = setInterval(async () => {
      try {
        const data = await vexaAPI.getRecordingFrames(recordingId);
        setExtractionStatus(data.extraction_status);
        setTotal(data.total);
        setFailureReason(data.failure_reason ?? null);
        // Append new frames (compare by id to avoid re-rendering existing)
        setFrames(prev => {
          const existingIds = new Set(prev.map(f => f.id));
          const newFrames = data.frames.filter(f => !existingIds.has(f.id));
          if (newFrames.length === 0) return prev;
          return [...prev, ...newFrames];
        });
      } catch {
        // Silently continue polling on error
      }
    }, 5000);

    return () => clearInterval(interval);
  }, [extractionStatus, recordingId]);

  // Handle click on a snapshot thumbnail
  const handleSnapshotClick = useCallback(async (frame: RecordingFrame, index: number) => {
    setFocusedIndex(index);

    // Check if presigned URL is expired — if so, refresh it
    if (frame.expires_at) {
      const expiresAt = new Date(frame.expires_at).getTime();
      if (Date.now() >= expiresAt) {
        try {
          const fresh = await vexaAPI.getFrameUrl(recordingId, frame.id);
          // Update the frame URL in-place
          setFrames(prev =>
            prev.map(f => (f.id === frame.id ? { ...f, url: fresh.url, expires_at: fresh.expires_at } : f))
          );
          onTimestampClick?.(frame.timestamp_s);
        } catch {
          // Even if refresh fails, still try to seek
          onTimestampClick?.(frame.timestamp_s);
        }
        return;
      }
    }

    onTimestampClick?.(frame.timestamp_s);
  }, [onTimestampClick, recordingId]);

  // Keyboard navigation (WAI-ARIA grid pattern)
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!frames.length) return;

    let newIndex = focusedIndex;
    if (e.key === "ArrowRight") {
      newIndex = Math.min(focusedIndex + 1, frames.length - 1);
    } else if (e.key === "ArrowLeft") {
      newIndex = Math.max(focusedIndex - 1, 0);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSnapshotClick(frames[focusedIndex], focusedIndex);
      return;
    } else if (e.key === "Escape") {
      gridRef.current?.focus();
      return;
    } else {
      return;
    }

    e.preventDefault();
    setFocusedIndex(newIndex);
    // Focus the new cell
    const cell = gridRef.current?.querySelector(`[data-index="${newIndex}"]`) as HTMLElement;
    cell?.focus();
  }, [focusedIndex, frames, handleSnapshotClick]);

  // --- Empty states ---

  // Hide gallery when snapshots disabled (API returned 404) and no video
  if (extractionStatus === null) {
    // Still loading
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Snapshots</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="aspect-video rounded-md" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (extractionStatus === "none") {
    if (!hasVideo) {
      return (
        <Card className={className}>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Snapshots</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <ImageIcon className="h-10 w-10 text-muted-foreground/50 mb-3" />
              <p className="text-sm text-muted-foreground">
                No snapshots — this meeting was recorded audio-only
              </p>
            </div>
          </CardContent>
        </Card>
      );
    }
    // Video exists but no frames (snapshots disabled or extraction not started)
    return null;
  }

  if (extractionStatus === "processing" && frames.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Snapshots</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Loader2 className="h-8 w-8 text-muted-foreground animate-spin mb-3" />
            <p className="text-sm text-muted-foreground">
              Snapshots are being generated…
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (extractionStatus === "failed") {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Snapshots</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <AlertCircle className="h-8 w-8 text-muted-foreground/50 mb-3" />
            <p className="text-sm text-muted-foreground">
              Snapshots could not be generated
            </p>
            {failureReason && (
              <p className="text-xs text-muted-foreground/70 mt-1">{failureReason}</p>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Snapshots</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <AlertCircle className="h-8 w-8 text-destructive/50 mb-3" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <button
              className="mt-2 text-xs text-primary hover:underline inline-flex items-center gap-1"
              onClick={() => window.location.reload()}
            >
              <RefreshCw className="h-3 w-3" />
              Retry
            </button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (frames.length === 0 && extractionStatus === "complete") {
    return null;
  }

  // --- Thumbnail grid ---

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-sm font-medium">
          Snapshots
          {total > 0 && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {frames.length} of {total}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div
          ref={gridRef}
          role="grid"
          aria-label="Meeting snapshots"
          className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-2"
          onKeyDown={handleKeyDown}
          tabIndex={0}
        >
          {frames.map((frame, index) => (
            <div
              key={frame.id}
              data-index={index}
              role="gridcell"
              tabIndex={index === focusedIndex ? 0 : -1}
              aria-label={`Snapshot at ${formatTime(frame.timestamp_s)}`}
              className={cn(
                "relative aspect-video rounded-md overflow-hidden cursor-pointer group",
                "ring-2 ring-transparent hover:ring-primary hover:opacity-90 transition-all",
                index === focusedIndex && "ring-primary"
              )}
              onClick={() => handleSnapshotClick(frame, index)}
              onFocus={() => setFocusedIndex(index)}
            >
              <img
                src={frame.url}
                alt={`Snapshot at ${formatTime(frame.timestamp_s)}`}
                loading="lazy"
                className="w-full h-full object-cover"
              />
              <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/70 to-transparent px-1.5 py-0.5">
                <span className="text-[10px] font-mono text-white">
                  {formatTime(frame.timestamp_s)}
                </span>
              </div>
            </div>
          ))}
        </div>
        {extractionStatus === "processing" && (
          <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            Generating more snapshots…
          </div>
        )}
      </CardContent>
    </Card>
  );
}