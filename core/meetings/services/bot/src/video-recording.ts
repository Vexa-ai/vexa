/**
 * Recording wiring (2b) — the server-side ffmpeg-for-all capture path.
 *
 * ONE ffmpeg (RecordingCaptureService) captures the meeting audio (record_sink.monitor) — plus the
 * bot's Xvfb display when the invocation asks for video — and writes live MPEG-DASH. It is the SOLE
 * recording path for both audio-only and A+V meetings: no browser MediaRecorder, no separate x11grab.
 *
 * Because each bot runs in its own container with its own Xvfb, concurrent bots never capture each
 * other's screen — the shared-display cross-contamination of the pre-restructure design cannot occur.
 *
 * Gating: recordingEnabled always records audio; captureModes including "video" adds the display.
 * L4-gated (needs ffmpeg + PulseAudio + a live X display) — exercised on the VM run, not unit tests.
 * Every path here is best-effort: a recording fault must never change the bot's join/leave/exit.
 */
import * as fs from 'fs';
import { RecordingService, RecordingCaptureService } from '@vexa/recording';
import type { Invocation } from './config.js';

/** True when this invocation asks the recording to also capture the display (recording on + "video"). */
export function wantsVideoCapture(inv: Invocation): boolean {
  return !!inv.recordingEnabled && Array.isArray(inv.captureModes) && inv.captureModes.includes('video');
}

/**
 * Start ffmpeg-for-all recording: one RecordingCaptureService captures the meeting audio
 * (record_sink.monitor) — plus the Xvfb display when `withVideo` — and writes live MPEG-DASH. Each
 * DASH file uploads to the recordings `/dash` endpoint as it lands (serialized), and a final manifest
 * upload (is_final) flips the recording COMPLETED. Returns an idempotent best-effort stop.
 *
 * Call from pipeline.start() (post-admission) so capture begins once the live meeting is rendering;
 * call the returned stop from pipeline.stop() and again in the composition-root teardown (no-op after
 * the first call).
 */
export function startFfmpegRecording(inv: Invocation, withVideo: boolean, log: (m: string) => void): () => Promise<void> {
  const meetingId = inv.meeting_id ?? 0;
  const sessionUid = inv.connectionId ?? inv.nativeMeetingId ?? 'session';
  const url = inv.recordingUploadUrl;
  const hlsUrl = url ? url.replace(/\/upload$/, '/hls') : undefined; // sibling of the chunk endpoint
  const token = inv.internalSecret ?? '';
  const uploader = hlsUrl ? new RecordingService(meetingId, sessionUid) : null;
  let queue: Promise<void> = Promise.resolve();
  // The recorder's true t=0 (ffmpeg start, absolute ISO) — set once start() runs and sent with every
  // HLS upload so the server anchors first_chunk_at to it, keeping transcript↔video sync tight.
  let startedAtIso: string | undefined;

  let svc: RecordingCaptureService | null = null;
  try {
    svc = new RecordingCaptureService({ meetingId, sessionUid, withVideo });
    if (uploader && hlsUrl) {
      svc.onFile = (relpath, absPath) => {
        queue = queue.then(async () => {
          try {
            const bytes = await fs.promises.readFile(absPath);
            await uploader.uploadHlsFile(hlsUrl, token, bytes, relpath, false, startedAtIso, withVideo);
          } catch (e) {
            log(`ffmpeg-rec: upload ${relpath} failed — continuing: ${String(e)}`);
          }
        });
      };
    }
    svc.start();
    startedAtIso = new Date(svc.getStartTime()).toISOString();
    log(`ffmpeg-rec: started (video=${withVideo}, session ${sessionUid})`);
  } catch (e) {
    log(`ffmpeg-rec: start FAILED (session ${sessionUid}): ${String(e)}`);
    svc = null;
  }

  let stopped = false;
  return async () => {
    if (stopped || !svc) return;
    stopped = true;
    const s = svc;
    try {
      await s.stop();  // flushes remaining segments + the final playlist (with ENDLIST) via onFile
      await queue;     // drain uploads
      if (uploader && hlsUrl) {
        try {
          const bytes = await fs.promises.readFile(s.getPlaylistPath());
          await uploader.uploadHlsFile(hlsUrl, token, bytes, 'playlist.m3u8', true, startedAtIso, withVideo); // is_final → COMPLETED
          log(`ffmpeg-rec: finalized (session ${sessionUid})`);
        } catch (e) {
          log(`ffmpeg-rec: finalize upload failed (session ${sessionUid}): ${String(e)}`);
        }
      }
    } catch (e) {
      log(`ffmpeg-rec: stop/upload FAILED (session ${sessionUid}): ${String(e)}`);
    } finally {
      await s.cleanup().catch(() => { /* best-effort */ });
    }
  };
}
