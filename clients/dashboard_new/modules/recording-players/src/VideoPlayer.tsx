/**
 * VideoPlayer.tsx — presentational video player for a meeting recording.
 *
 * Props in, DOM out. NO store, NO fetch, NO websocket — the URL is injected by the caller via `src`.
 * Play/pause (click on the video or the control), seek (range scrubber), mute, and fullscreen, with a
 * `seekTo(seconds)` imperative handle for a transcript to jump the playhead.
 *
 * This is the CLEAN modular re-build of dashboard/src/components/recording/video-player.tsx: same
 * behaviour but self-contained markup/styles (no shadcn Button, no `cn`, no lucide), so the brick is
 * dependency-light and drop-in renderable.
 */
import {
  useRef,
  useState,
  useEffect,
  useImperativeHandle,
  forwardRef,
} from "react";

export interface VideoPlayerHandle {
  /** Seek the playhead to `seconds` and start playing. */
  seekTo: (seconds: number) => void;
}

export interface VideoPlayerProps {
  /** URL to stream the video (injected by the caller). */
  src: string;
  className?: string;
  /** Fired with the current time, in seconds. */
  onTimeUpdate?: (currentTime: number) => void;
}

function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Inline SVG control glyphs (no icon dependency — the brick stays dependency-free). All draw in
//    `currentColor` so they inherit the control bar's text color (white over the video gradient). ────
function PlayIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M4 3.5v9l8-4.5-8-4.5z" fill="currentColor" />
    </svg>
  );
}
function PauseIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="4" y="3" width="3" height="10" rx="1" fill="currentColor" />
      <rect x="9" y="3" width="3" height="10" rx="1" fill="currentColor" />
    </svg>
  );
}
function VolumeIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3 6h2.5L9 3v10L5.5 10H3V6z" fill="currentColor" />
      <path
        d="M11 5.5a3.5 3.5 0 0 1 0 5M12.5 4a5.5 5.5 0 0 1 0 8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
function MuteIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3 6h2.5L9 3v10L5.5 10H3V6z" fill="currentColor" />
      <path
        d="M11 6l3 3M14 6l-3 3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
function FullscreenIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(
  function VideoPlayer({ src, className, onTimeUpdate }, ref) {
    const videoRef = useRef<HTMLVideoElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [isMuted, setIsMuted] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [isLoaded, setIsLoaded] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useImperativeHandle(ref, () => ({
      seekTo: (seconds: number) => {
        const video = videoRef.current;
        if (!video) return;
        video.currentTime = seconds;
        setCurrentTime(seconds);
        video.play().catch(() => {});
      },
    }));

    useEffect(() => {
      const video = videoRef.current;
      if (!video) return;

      const onLoadedMetadata = () => {
        setDuration(video.duration);
        setIsLoaded(true);
        setError(null);
      };
      const handleTimeUpdate = () => {
        setCurrentTime(video.currentTime);
        onTimeUpdate?.(video.currentTime);
      };
      const onPlay = () => setIsPlaying(true);
      const onPause = () => setIsPlaying(false);
      const onEnded = () => setIsPlaying(false);
      const onError = () => setError("Failed to load video");

      video.addEventListener("loadedmetadata", onLoadedMetadata);
      video.addEventListener("timeupdate", handleTimeUpdate);
      video.addEventListener("play", onPlay);
      video.addEventListener("pause", onPause);
      video.addEventListener("ended", onEnded);
      video.addEventListener("error", onError);

      if (video.readyState >= 1 /* HAVE_METADATA */) onLoadedMetadata();

      return () => {
        video.removeEventListener("loadedmetadata", onLoadedMetadata);
        video.removeEventListener("timeupdate", handleTimeUpdate);
        video.removeEventListener("play", onPlay);
        video.removeEventListener("pause", onPause);
        video.removeEventListener("ended", onEnded);
        video.removeEventListener("error", onError);
      };
    }, [onTimeUpdate]);

    const togglePlay = () => {
      const video = videoRef.current;
      if (!video) return;
      if (video.paused) video.play().catch(() => {});
      else video.pause();
    };

    const toggleMute = () => {
      const video = videoRef.current;
      if (!video) return;
      video.muted = !video.muted;
      setIsMuted(video.muted);
    };

    const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
      const video = videoRef.current;
      if (!video) return;
      const value = parseFloat(e.target.value);
      video.currentTime = value;
      setCurrentTime(value);
    };

    const handleFullscreen = () => {
      videoRef.current?.requestFullscreen?.();
    };

    return (
      <div
        data-testid="video-player"
        className={className}
        style={{
          position: "relative",
          borderRadius: 8,
          overflow: "hidden",
          background: "#000",
          fontFamily: "var(--font-sans, system-ui, sans-serif)",
          fontSize: 13,
        }}
      >
        {/* The element under test: a real <video> with the injected source. */}
        <video
          ref={videoRef}
          src={src}
          data-testid="video-el"
          preload="metadata"
          onClick={togglePlay}
          style={{ width: "100%", aspectRatio: "16 / 9", objectFit: "contain", display: "block" }}
        />

        <div
          style={{
            position: "absolute",
            insetInline: 0,
            bottom: 0,
            padding: 12,
            background: "linear-gradient(to top, rgba(0,0,0,0.8), transparent)",
          }}
        >
          <input
            type="range"
            aria-label="Seek"
            data-testid="seek-bar"
            value={currentTime}
            min={0}
            max={duration || 1}
            step={0.1}
            onChange={handleSeek}
            disabled={!isLoaded}
            style={{
              width: "100%",
              cursor: "pointer",
              marginBottom: 8,
              display: "block",
              // filled portion → primary accent (theme token; white fallback reads on the dark gradient)
              accentColor: "var(--primary, #ffffff)",
            }}
          />

          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#fff" }}>
            <button
              type="button"
              aria-label={isPlaying ? "Pause" : "Play"}
              data-testid="play-toggle"
              onClick={togglePlay}
              disabled={!isLoaded}
              style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, border: "none", borderRadius: 6, background: "transparent", color: "#fff", cursor: "pointer" }}
            >
              {isPlaying ? <PauseIcon /> : <PlayIcon />}
            </button>

            <button
              type="button"
              aria-label={isMuted ? "Unmute" : "Mute"}
              data-testid="mute-toggle"
              onClick={toggleMute}
              style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, border: "none", borderRadius: 6, background: "transparent", color: "#fff", cursor: "pointer" }}
            >
              {isMuted ? <MuteIcon /> : <VolumeIcon />}
            </button>

            <span data-testid="time-label" style={{ flex: "1 1 auto", fontVariantNumeric: "tabular-nums", opacity: 0.8 }}>
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>

            <button
              type="button"
              aria-label="Fullscreen"
              data-testid="fullscreen"
              onClick={handleFullscreen}
              style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, border: "none", borderRadius: 6, background: "transparent", color: "#fff", cursor: "pointer" }}
            >
              <FullscreenIcon />
            </button>
          </div>
        </div>

        {error && (
          <div
            data-testid="video-error"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "rgba(0,0,0,0.7)",
              color: "rgba(255,255,255,0.8)",
            }}
          >
            {error}
          </div>
        )}
      </div>
    );
  },
);

VideoPlayer.displayName = "VideoPlayer";
