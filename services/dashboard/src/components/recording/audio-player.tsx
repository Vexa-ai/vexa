"use client";

import { useRef, useState, useEffect, useCallback, forwardRef, useImperativeHandle } from "react";
import { Play, Pause, Volume2, VolumeX, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Describes a single recording fragment in a multi-fragment timeline.
 * Fragments are played sequentially; their durations define the virtual timeline.
 */
export interface AudioFragment {
  /** URL to stream this fragment's audio */
  src: string;
  /** Duration in seconds (from media_files[].duration_seconds). 0 or null means unknown. */
  duration: number;
  /** Session UID this fragment belongs to */
  sessionUid: string;
  /** ISO timestamp when this recording started (from recordings[].created_at) */
  createdAt: string;
}

export interface AudioPlayerHandle {
  /**
   * Seek to a specific time in a specific fragment.
   * @param fragmentIndex Which fragment to seek into
   * @param timeInFragment Seconds offset within that fragment
   */
  seekToFragment: (fragmentIndex: number, timeInFragment: number) => void;
  /** Legacy: seek by virtual (stitched) time across all fragments */
  seekTo: (time: number) => void;
}

interface AudioPlayerProps {
  /** Single source URL (legacy, for single-recording meetings) */
  src?: string;
  /** Multiple ordered fragments for multi-recording meetings */
  fragments?: AudioFragment[];
  onTimeUpdate?: (currentTime: number) => void;
  /** Called with the index of the currently playing fragment */
  onFragmentChange?: (fragmentIndex: number) => void;
  className?: string;
  compact?: boolean;
}

function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export const AudioPlayer = forwardRef<AudioPlayerHandle, AudioPlayerProps>(
  function AudioPlayer({ src, fragments, onTimeUpdate, onFragmentChange, className, compact = false }, ref) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [isMuted, setIsMuted] = useState(false);
    const [errorCount, setErrorCount] = useState(0);

    // Multi-fragment state
    const [currentFragmentIndex, setCurrentFragmentIndex] = useState(0);
    const [fragmentDurations, setFragmentDurations] = useState<number[]>([]);
    const wasPlayingRef = useRef(false);

    // Determine if we're in multi-fragment mode
    const isMultiFragment = fragments && fragments.length > 1;
    const effectiveFragments = fragments && fragments.length > 0 ? fragments : src ? [{ src, duration: 0, sessionUid: "", createdAt: "" }] : [];
    const currentFragment = effectiveFragments[currentFragmentIndex];
    const currentSrc = currentFragment?.src || src || "";

    // Compute total virtual duration from known fragment durations
    const totalDuration = isMultiFragment
      ? fragmentDurations.reduce((sum, d) => sum + d, 0)
      : duration;

    // Compute virtual time offset for the current fragment
    const virtualOffset = isMultiFragment
      ? fragmentDurations.slice(0, currentFragmentIndex).reduce((sum, d) => sum + d, 0)
      : 0;

    // Virtual current time = offset of current fragment + time within current fragment
    const virtualCurrentTime = virtualOffset + currentTime;

    // Update fragment durations when actual audio metadata loads
    const updateFragmentDuration = useCallback((index: number, dur: number) => {
      setFragmentDurations(prev => {
        const updated = [...prev];
        updated[index] = dur;
        return updated;
      });
    }, []);

    // Initialize fragment durations from props
    useEffect(() => {
      if (effectiveFragments.length > 0) {
        setFragmentDurations(effectiveFragments.map(f => f.duration || 0));
      }
    }, [fragments, src]); // eslint-disable-line react-hooks/exhaustive-deps

    // Seek to a specific fragment and time within it
    const seekToFragment = useCallback((fragmentIndex: number, timeInFragment: number) => {
      const audio = audioRef.current;
      if (!audio) return;
      if (fragmentIndex < 0 || fragmentIndex >= effectiveFragments.length) return;

      if (fragmentIndex === currentFragmentIndex) {
        // Same fragment — just seek within it
        audio.currentTime = timeInFragment;
        setCurrentTime(timeInFragment);
        if (audio.paused) {
          audio.play().catch(() => {});
        }
      } else {
        // Different fragment — switch source then seek
        wasPlayingRef.current = true;
        setCurrentFragmentIndex(fragmentIndex);
        setCurrentTime(timeInFragment);
        // The seek will happen in the effect that handles fragment changes
      }
    }, [currentFragmentIndex, effectiveFragments.length]);

    // Expose seekTo / seekToFragment to parent via ref
    useImperativeHandle(ref, () => ({
      seekToFragment,
      seekTo(time: number) {
        if (!isMultiFragment) {
          const audio = audioRef.current;
          if (!audio) return;
          audio.currentTime = time;
          setCurrentTime(time);
          if (audio.paused) {
            audio.play().catch(() => {});
          }
          return;
        }
        // Multi-fragment: find which fragment this virtual time falls in
        let remaining = time;
        for (let i = 0; i < fragmentDurations.length; i++) {
          if (remaining <= fragmentDurations[i] || i === fragmentDurations.length - 1) {
            seekToFragment(i, remaining);
            return;
          }
          remaining -= fragmentDurations[i];
        }
      },
    }), [isMultiFragment, fragmentDurations, seekToFragment]);

    // When fragment index changes, update the audio source and seek
    const pendingSeekRef = useRef<number | null>(null);
    // Track which src we've loaded — initialized to the first src so we skip
    // the redundant load on mount (the <audio> element already has src in JSX).
    const lastLoadedSrcRef = useRef<string>(currentSrc);

    useEffect(() => {
      const audio = audioRef.current;
      if (!audio || !currentSrc) return;

      // If the source hasn't changed, skip (avoids double-load on mount)
      if (lastLoadedSrcRef.current === currentSrc) return;
      lastLoadedSrcRef.current = currentSrc;

      pendingSeekRef.current = currentTime;
      setIsLoading(true);
      audio.src = currentSrc;
      audio.load();
      onFragmentChange?.(currentFragmentIndex);
    }, [currentFragmentIndex, currentSrc]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
      const audio = audioRef.current;
      if (!audio) return;

      const handleTimeUpdate = () => {
        const time = audio.currentTime;
        setCurrentTime(time);
        // Report virtual time to parent
        if (isMultiFragment) {
          const offset = fragmentDurations.slice(0, currentFragmentIndex).reduce((s, d) => s + d, 0);
          onTimeUpdate?.(offset + time);
        } else {
          onTimeUpdate?.(time);
        }
      };

      const handleLoadedMetadata = () => {
        const dur = audio.duration;
        setDuration(dur);
        updateFragmentDuration(currentFragmentIndex, dur);
        setIsLoading(false);

        // If we have a pending seek from a fragment switch, apply it now
        if (pendingSeekRef.current !== null) {
          audio.currentTime = pendingSeekRef.current;
          pendingSeekRef.current = null;
        }
        // Auto-play if we were playing before fragment switch
        if (wasPlayingRef.current) {
          audio.play().catch(() => {});
          wasPlayingRef.current = false;
        }
      };

      const handleCanPlay = () => {
        setIsLoading(false);
        setErrorCount(0);
      };
      const handleWaiting = () => setIsLoading(true);
      const handlePlaying = () => { setIsLoading(false); setIsPlaying(true); };
      const handlePause = () => setIsPlaying(false);
      const handleEnded = () => {
        // Multi-fragment: auto-advance to next fragment
        if (isMultiFragment && currentFragmentIndex < effectiveFragments.length - 1) {
          wasPlayingRef.current = true;
          setCurrentTime(0);
          setCurrentFragmentIndex(prev => prev + 1);
        } else {
          setIsPlaying(false);
        }
      };
      const handleError = () => {
        setIsPlaying(false);
        setIsLoading(true);
        setErrorCount((count) => count + 1);
        if (retryTimerRef.current) {
          clearTimeout(retryTimerRef.current);
        }
        retryTimerRef.current = setTimeout(() => {
          audio.load();
        }, 1500);
      };

      audio.addEventListener("timeupdate", handleTimeUpdate);
      audio.addEventListener("loadedmetadata", handleLoadedMetadata);
      audio.addEventListener("canplay", handleCanPlay);
      audio.addEventListener("waiting", handleWaiting);
      audio.addEventListener("playing", handlePlaying);
      audio.addEventListener("pause", handlePause);
      audio.addEventListener("ended", handleEnded);
      audio.addEventListener("error", handleError);

      return () => {
        if (retryTimerRef.current) {
          clearTimeout(retryTimerRef.current);
          retryTimerRef.current = null;
        }
        audio.removeEventListener("timeupdate", handleTimeUpdate);
        audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
        audio.removeEventListener("canplay", handleCanPlay);
        audio.removeEventListener("waiting", handleWaiting);
        audio.removeEventListener("playing", handlePlaying);
        audio.removeEventListener("pause", handlePause);
        audio.removeEventListener("ended", handleEnded);
        audio.removeEventListener("error", handleError);
      };
    }, [onTimeUpdate, isMultiFragment, currentFragmentIndex, effectiveFragments.length, fragmentDurations, updateFragmentDuration]);

    const togglePlay = useCallback(() => {
      const audio = audioRef.current;
      if (!audio) return;
      if (isPlaying) {
        audio.pause();
      } else {
        audio.play().catch(() => {
          setErrorCount((count) => count + 1);
        });
      }
    }, [isPlaying]);

    const toggleMute = useCallback(() => {
      const audio = audioRef.current;
      if (!audio) return;
      audio.muted = !audio.muted;
      setIsMuted(!isMuted);
    }, [isMuted]);

    const handleSeekBarChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
      const time = parseFloat(e.target.value);
      if (!isMultiFragment) {
        const audio = audioRef.current;
        if (audio) {
          audio.currentTime = time;
          setCurrentTime(time);
        }
        return;
      }
      // Multi-fragment: map virtual time to fragment + offset
      let remaining = time;
      for (let i = 0; i < fragmentDurations.length; i++) {
        if (remaining <= fragmentDurations[i] || i === fragmentDurations.length - 1) {
          seekToFragment(i, remaining);
          return;
        }
        remaining -= fragmentDurations[i];
      }
    }, [isMultiFragment, fragmentDurations, seekToFragment]);

    const displayDuration = isMultiFragment ? totalDuration : duration;
    const displayTime = isMultiFragment ? virtualCurrentTime : currentTime;
    const progress = displayDuration > 0 ? (displayTime / displayDuration) * 100 : 0;

    // Build fragment indicators for the seek bar
    const fragmentMarkers = isMultiFragment && totalDuration > 0
      ? fragmentDurations.reduce<number[]>((acc, dur, i) => {
          if (i < fragmentDurations.length - 1) {
            const pos = acc.length > 0 ? acc[acc.length - 1] : 0;
            acc.push(pos + dur);
          }
          return acc;
        }, [])
      : [];

    return (
      <div
        className={cn(
          "flex items-center bg-muted/50 rounded-lg border",
          compact ? "gap-1.5 px-2 py-1" : "gap-3 px-4 py-2",
          className
        )}
      >
        <audio ref={audioRef} src={currentSrc} preload="metadata" />

        {/* Play/Pause */}
        <Button
          variant="ghost"
          size="icon"
          className={cn("shrink-0", compact ? "h-6 w-6" : "h-8 w-8")}
          onClick={togglePlay}
          disabled={isLoading && !isPlaying}
        >
          {isLoading && !isPlaying ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : isPlaying ? (
            <Pause className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
        </Button>

        {/* Current time */}
        <span className={cn("text-xs text-muted-foreground tabular-nums text-right shrink-0", compact ? "w-8" : "w-10")}>
          {formatTime(displayTime)}
        </span>

        {/* Seek bar */}
        <div className={cn("relative flex-1 flex items-center", compact ? "h-6" : "h-8")}>
          <div className="absolute inset-x-0 h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-[width] duration-75"
              style={{ width: `${progress}%` }}
            />
          </div>
          {/* Fragment boundary markers */}
          {fragmentMarkers.map((pos, i) => (
            <div
              key={i}
              className="absolute top-1/2 -translate-y-1/2 w-0.5 h-3 bg-muted-foreground/40 rounded-full"
              style={{ left: `${(pos / totalDuration) * 100}%` }}
              title={`Fragment ${i + 2}`}
            />
          ))}
          <input
            type="range"
            min={0}
            max={displayDuration || 0}
            step={0.1}
            value={displayTime}
            onChange={handleSeekBarChange}
            className={cn("absolute inset-x-0 w-full opacity-0 cursor-pointer", compact ? "h-6" : "h-8")}
          />
        </div>

        {/* Duration */}
        <span className={cn("text-xs text-muted-foreground tabular-nums shrink-0", compact ? "w-8" : "w-10")}>
          {formatTime(displayDuration)}
        </span>

        {/* Fragment indicator (compact) */}
        {isMultiFragment && (
          <span className={cn("text-[10px] text-muted-foreground tabular-nums shrink-0", compact ? "" : "")}>
            {currentFragmentIndex + 1}/{effectiveFragments.length}
          </span>
        )}

        {/* Mute */}
        <Button
          variant="ghost"
          size="icon"
          className={cn("shrink-0", compact ? "h-6 w-6" : "h-8 w-8")}
          onClick={toggleMute}
        >
          {isMuted ? (
            <VolumeX className="h-4 w-4" />
          ) : (
            <Volume2 className="h-4 w-4" />
          )}
        </Button>

        {isLoading && errorCount > 0 && (
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            Preparing audio...
          </span>
        )}
      </div>
    );
  }
);
