/**
 * @vexa/dash-recording-players — front door.
 *
 * Presentational React players for a meeting recording. Props in, DOM out: no store, no fetch, no
 * websocket — the caller injects the media URL(s). Typed by the recording fields @vexa/dash-contracts
 * exposes (RecordingMaster.raw_url / duration_seconds), re-expressed as the players' props.
 *
 *   • AudioPlayer  — { src? | fragments?, onTimeUpdate?, onFragmentChange? } → an <audio> + controls,
 *                    with a stitched virtual timeline across multi-recording fragments.
 *   • VideoPlayer  — { src, onTimeUpdate? } → a <video> + controls, with a seekTo(seconds) handle.
 */
export { AudioPlayer } from "./AudioPlayer.js";
export type {
  AudioPlayerProps,
  AudioPlayerHandle,
  AudioFragment,
} from "./AudioPlayer.js";

export { VideoPlayer } from "./VideoPlayer.js";
export type { VideoPlayerProps, VideoPlayerHandle } from "./VideoPlayer.js";
