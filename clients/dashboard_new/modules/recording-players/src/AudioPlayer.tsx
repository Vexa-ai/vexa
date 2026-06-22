/**
 * AudioPlayer.tsx — presentational audio player for a meeting recording.
 *
 * Props in, DOM out. NO store, NO fetch, NO websocket — the URL(s) are injected by the caller.
 * A single recording is driven by `src`; a multi-recording meeting is driven by ordered `fragments`
 * (played sequentially, their durations forming one virtual timeline). When both are given, `fragments`
 * wins; when neither, the player renders disabled.
 *
 * This is the CLEAN modular re-build of dashboard/src/components/recording/audio-player.tsx: same
 * behaviour (play/pause, seek, mute, fragment auto-advance, virtual stitched timeline) but with
 * self-contained markup/styles (no shadcn Button, no `cn`, no lucide) so the brick is dependency-light
 * and drop-in renderable. The fragment shape is typed by @vexa/dash-contracts' RecordingMaster-derived
 * fields (`duration_seconds`) re-expressed as the player's `AudioFragment`.
 */
import {
  useRef,
  useState,
  useEffect,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";

/**
 * A single recording fragment in a multi-fragment timeline. Mirrors the fields the player needs from
 * @vexa/dash-contracts' `RecordingMaster` (`raw_url` → `src`, `duration_seconds` → `duration`) plus the
 * session/created metadata the caller already holds.
 */
export interface AudioFragment {
  /** URL to stream this fragment's audio (RecordingMaster.raw_url). */
  src: string;
  /** Duration in seconds (RecordingMaster.duration_seconds). 0 means unknown until metadata loads. */
  duration: number;
  /** Session UID this fragment belongs to. */
  sessionUid?: string;
  /** ISO timestamp when this recording started. */
  createdAt?: string;
}

export interface AudioPlayerHandle {
  /** Seek to `timeInFragment` seconds within fragment `fragmentIndex`. */
  seekToFragment: (fragmentIndex: number, timeInFragment: number) => void;
  /** Seek by virtual (stitched) time across all fragments. */
  seekTo: (time: number) => void;
}

export interface AudioPlayerProps {
  /** Single source URL (single-recording meetings). */
  src?: string;
  /** Ordered fragments for multi-recording meetings (takes precedence over `src`). */
  fragments?: AudioFragment[];
  /** Fired with the virtual (stitched) current time, in seconds. */
  onTimeUpdate?: (currentTime: number) => void;
  /** Fired with the index of the fragment that just became current. */
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
  function AudioPlayer(
    { src, fragments, onTimeUpdate, onFragmentChange, className, compact = false },
    ref,
  ) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [isMuted, setIsMuted] = useState(false);

    const [currentFragmentIndex, setCurrentFragmentIndex] = useState(0);
    const [fragmentDurations, setFragmentDurations] = useState<number[]>([]);
    const wasPlayingRef = useRef(false);

    const isMultiFragment = !!fragments && fragments.length > 1;
    const effectiveFragments: AudioFragment[] =
      fragments && fragments.length > 0
        ? fragments
        : src
        ? [{ src, duration: 0 }]
        : [];
    const currentFragment = effectiveFragments[currentFragmentIndex];
    const currentSrc = currentFragment?.src || src || "";
    const hasSource = currentSrc !== "";

    const totalDuration = isMultiFragment
      ? fragmentDurations.reduce((sum, d) => sum + d, 0)
      : duration;
    const virtualOffset = isMultiFragment
      ? fragmentDurations
          .slice(0, currentFragmentIndex)
          .reduce((sum, d) => sum + d, 0)
      : 0;
    const virtualCurrentTime = virtualOffset + currentTime;

    const updateFragmentDuration = useCallback((index: number, dur: number) => {
      setFragmentDurations((prev) => {
        if (prev[index] === dur) return prev;
        const updated = [...prev];
        updated[index] = dur;
        return updated;
      });
    }, []);

    // Seed fragment durations from props.
    useEffect(() => {
      if (effectiveFragments.length > 0) {
        const durations = effectiveFragments.map((f) => f.duration || 0);
        setFragmentDurations((prev) =>
          prev.length === durations.length &&
          prev.every((v, i) => v === durations[i])
            ? prev
            : durations,
        );
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fragments, src]);

    const seekToFragment = useCallback(
      (fragmentIndex: number, timeInFragment: number) => {
        const audio = audioRef.current;
        if (!audio) return;
        if (fragmentIndex < 0 || fragmentIndex >= effectiveFragments.length) return;

        if (fragmentIndex === currentFragmentIndex) {
          audio.currentTime = timeInFragment;
          setCurrentTime(timeInFragment);
          if (audio.paused) audio.play().catch(() => {});
        } else {
          wasPlayingRef.current = true;
          setCurrentFragmentIndex(fragmentIndex);
          setCurrentTime(timeInFragment);
        }
      },
      [currentFragmentIndex, effectiveFragments.length],
    );

    useImperativeHandle(
      ref,
      () => ({
        seekToFragment,
        seekTo(time: number) {
          if (!isMultiFragment) {
            const audio = audioRef.current;
            if (!audio) return;
            audio.currentTime = time;
            setCurrentTime(time);
            if (audio.paused) audio.play().catch(() => {});
            return;
          }
          let remaining = time;
          for (let i = 0; i < fragmentDurations.length; i++) {
            if (remaining <= fragmentDurations[i] || i === fragmentDurations.length - 1) {
              seekToFragment(i, remaining);
              return;
            }
            remaining -= fragmentDurations[i];
          }
        },
      }),
      [isMultiFragment, fragmentDurations, seekToFragment],
    );

    const pendingSeekRef = useRef<number | null>(null);
    const lastLoadedSrcRef = useRef<string>(currentSrc);

    // On fragment change, swap source + load.
    useEffect(() => {
      const audio = audioRef.current;
      if (!audio || !currentSrc) return;
      if (lastLoadedSrcRef.current === currentSrc) return;
      lastLoadedSrcRef.current = currentSrc;

      pendingSeekRef.current = currentTime;
      setIsLoading(true);
      audio.src = currentSrc;
      audio.load();
      onFragmentChange?.(currentFragmentIndex);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentFragmentIndex, currentSrc]);

    // Wire media events.
    useEffect(() => {
      const audio = audioRef.current;
      if (!audio) return;

      const handleTimeUpdate = () => {
        const time = audio.currentTime;
        setCurrentTime(time);
        if (isMultiFragment) {
          const offset = fragmentDurations
            .slice(0, currentFragmentIndex)
            .reduce((s, d) => s + d, 0);
          onTimeUpdate?.(offset + time);
        } else {
          onTimeUpdate?.(time);
        }
      };

      const syncMetadata = () => {
        const dur =
          Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
        setDuration(dur);
        updateFragmentDuration(currentFragmentIndex, dur);
        setIsLoading(false);
      };

      const handleLoadedMetadata = () => {
        syncMetadata();
        if (pendingSeekRef.current !== null) {
          audio.currentTime = pendingSeekRef.current;
          pendingSeekRef.current = null;
        }
        if (wasPlayingRef.current) {
          audio.play().catch(() => {});
          wasPlayingRef.current = false;
        }
      };

      const handleCanPlay = () => setIsLoading(false);
      const handlePlaying = () => {
        setIsLoading(false);
        setIsPlaying(true);
      };
      const handlePause = () => setIsPlaying(false);
      const handleEnded = () => {
        if (isMultiFragment && currentFragmentIndex < effectiveFragments.length - 1) {
          wasPlayingRef.current = true;
          setCurrentTime(0);
          setCurrentFragmentIndex((prev) => prev + 1);
        } else {
          setIsPlaying(false);
        }
      };

      audio.addEventListener("timeupdate", handleTimeUpdate);
      audio.addEventListener("loadedmetadata", handleLoadedMetadata);
      audio.addEventListener("durationchange", syncMetadata);
      audio.addEventListener("canplay", handleCanPlay);
      audio.addEventListener("playing", handlePlaying);
      audio.addEventListener("pause", handlePause);
      audio.addEventListener("ended", handleEnded);

      // Reconcile if the browser already loaded metadata before listeners attached.
      if (audio.readyState >= 1 /* HAVE_METADATA */) syncMetadata();
      if (audio.readyState >= 2 /* HAVE_CURRENT_DATA */) handleCanPlay();

      return () => {
        audio.removeEventListener("timeupdate", handleTimeUpdate);
        audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
        audio.removeEventListener("durationchange", syncMetadata);
        audio.removeEventListener("canplay", handleCanPlay);
        audio.removeEventListener("playing", handlePlaying);
        audio.removeEventListener("pause", handlePause);
        audio.removeEventListener("ended", handleEnded);
      };
    }, [
      onTimeUpdate,
      isMultiFragment,
      currentFragmentIndex,
      effectiveFragments.length,
      fragmentDurations,
      updateFragmentDuration,
    ]);

    const togglePlay = useCallback(() => {
      const audio = audioRef.current;
      if (!audio) return;
      if (isPlaying) audio.pause();
      else audio.play().catch(() => {});
    }, [isPlaying]);

    const toggleMute = useCallback(() => {
      const audio = audioRef.current;
      if (!audio) return;
      audio.muted = !audio.muted;
      setIsMuted(audio.muted);
    }, []);

    const handleSeekBarChange = useCallback(
      (e: React.ChangeEvent<HTMLInputElement>) => {
        const time = parseFloat(e.target.value);
        if (!isMultiFragment) {
          const audio = audioRef.current;
          if (audio) {
            audio.currentTime = time;
            setCurrentTime(time);
          }
          return;
        }
        let remaining = time;
        for (let i = 0; i < fragmentDurations.length; i++) {
          if (remaining <= fragmentDurations[i] || i === fragmentDurations.length - 1) {
            seekToFragment(i, remaining);
            return;
          }
          remaining -= fragmentDurations[i];
        }
      },
      [isMultiFragment, fragmentDurations, seekToFragment],
    );

    const displayDuration = isMultiFragment ? totalDuration : duration;
    const displayTime = isMultiFragment ? virtualCurrentTime : currentTime;
    const progress = displayDuration > 0 ? (displayTime / displayDuration) * 100 : 0;

    return (
      <div
        data-testid="audio-player"
        className={className}
        style={{
          display: "flex",
          alignItems: "center",
          gap: compact ? 6 : 12,
          padding: compact ? "4px 8px" : "8px 16px",
          background: "rgba(0,0,0,0.04)",
          border: "1px solid rgba(0,0,0,0.12)",
          borderRadius: 8,
          font: "13px system-ui, sans-serif",
        }}
      >
        {/* The element under test: a real <audio> with the injected source. */}
        <audio ref={audioRef} src={currentSrc} preload="metadata" data-testid="audio-el" />

        <button
          type="button"
          aria-label={isPlaying ? "Pause" : "Play"}
          data-testid="play-toggle"
          onClick={togglePlay}
          disabled={!hasSource || (isLoading && !isPlaying)}
          style={{
            flex: "0 0 auto",
            width: compact ? 24 : 32,
            height: compact ? 24 : 32,
            borderRadius: 6,
            border: "none",
            cursor: hasSource ? "pointer" : "default",
            background: "transparent",
          }}
        >
          {isPlaying ? "❚❚" : "▶"}
        </button>

        <span
          data-testid="current-time"
          style={{ flex: "0 0 auto", fontVariantNumeric: "tabular-nums", opacity: 0.7 }}
        >
          {formatTime(displayTime)}
        </span>

        <input
          type="range"
          aria-label="Seek"
          data-testid="seek-bar"
          min={0}
          max={displayDuration || 0}
          step={0.1}
          value={displayTime}
          onChange={handleSeekBarChange}
          disabled={!hasSource}
          style={{ flex: "1 1 auto", cursor: hasSource ? "pointer" : "default" }}
        />

        <span
          data-testid="duration"
          style={{ flex: "0 0 auto", fontVariantNumeric: "tabular-nums", opacity: 0.7 }}
        >
          {formatTime(displayDuration)}
        </span>

        {isMultiFragment && (
          <span
            data-testid="fragment-indicator"
            style={{ flex: "0 0 auto", fontSize: 11, opacity: 0.6 }}
          >
            {currentFragmentIndex + 1}/{effectiveFragments.length}
          </span>
        )}

        <button
          type="button"
          aria-label={isMuted ? "Unmute" : "Mute"}
          data-testid="mute-toggle"
          onClick={toggleMute}
          disabled={!hasSource}
          style={{
            flex: "0 0 auto",
            width: compact ? 24 : 32,
            height: compact ? 24 : 32,
            borderRadius: 6,
            border: "none",
            cursor: hasSource ? "pointer" : "default",
            background: "transparent",
          }}
        >
          {isMuted ? "🔇" : "🔊"}
        </button>
      </div>
    );
  },
);

AudioPlayer.displayName = "AudioPlayer";
