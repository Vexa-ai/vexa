"use client";

import { useRef, useState, useEffect, useImperativeHandle, forwardRef } from "react";
import { Play, Pause, Volume2, VolumeX, Maximize2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface VideoPlayerHandle {
  seekTo: (seconds: number) => void;
}

interface VideoPlayerProps {
  src: string;
  className?: string;
  onTimeUpdate?: (currentTime: number) => void;
}

export const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(
function VideoPlayer({ src, className, onTimeUpdate }, ref) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useImperativeHandle(ref, () => ({
    seekTo: (seconds: number) => {
      const video = videoRef.current;
      if (!video) return;
      video.currentTime = seconds;
      setCurrentTime(seconds);
      video.play();
    },
  }));
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isLoaded, setIsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    const onError = () => {
      const mediaError = video.error;
      setError(
        mediaError?.message ||
        (mediaError?.code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
          ? "Video format not supported"
          : "Failed to load video")
      );
    };

    video.addEventListener("loadedmetadata", onLoadedMetadata);
    video.addEventListener("timeupdate", handleTimeUpdate);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("ended", onEnded);
    video.addEventListener("error", onError);

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
    if (video.paused) {
      video.play();
    } else {
      video.pause();
    }
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

  const formatTime = (seconds: number) => {
    if (!isFinite(seconds)) return "0:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div className={cn("rounded-lg overflow-hidden bg-black group relative", className)}>
      <video
        ref={videoRef}
        src={src}
        className="w-full aspect-video object-contain"
        preload="metadata"
        onClick={togglePlay}
      />

      {/* Controls overlay */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-3 opacity-0 group-hover:opacity-100 transition-opacity">
        {/* Progress bar */}
        <input
          type="range"
          value={currentTime}
          min={0}
          max={duration || 1}
          step={0.1}
          onChange={handleSeek}
          disabled={!isLoaded}
          className="mb-2 w-full cursor-pointer accent-white h-1"
        />

        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-white hover:bg-white/20"
            onClick={togglePlay}
            disabled={!isLoaded}
          >
            {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-white hover:bg-white/20"
            onClick={toggleMute}
          >
            {isMuted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
          </Button>

          <span className="text-xs text-white/80 tabular-nums flex-1">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>

          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-white hover:bg-white/20"
            onClick={handleFullscreen}
          >
            <Maximize2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Error overlay */}
      {error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/70">
          <AlertCircle className="h-10 w-10 text-red-400 mb-2" />
          <p className="text-sm text-white/80">{error}</p>
        </div>
      )}

      {/* Play overlay when paused */}
      {!isPlaying && isLoaded && !error && (
        <div
          className="absolute inset-0 flex items-center justify-center cursor-pointer"
          onClick={togglePlay}
        >
          <div className="h-14 w-14 rounded-full bg-black/50 flex items-center justify-center">
            <Play className="h-7 w-7 text-white ml-1" />
          </div>
        </div>
      )}
    </div>
  );
});

VideoPlayer.displayName = "VideoPlayer";
