import { spawn, ChildProcess } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import http from 'http';
import https from 'https';
import { log } from '../utils';

/**
 * Hardware acceleration modes for video encoding.
 * Controlled via VIDEO_HWACCEL env var (default: 'none').
 *   none  — software encoding (libvpx-vp9 → webm). Works everywhere.
 *   vaapi — Intel iGPU / AMD via VA-API (h264_vaapi → mkv). Requires /dev/dri passthrough.
 *   nvenc — NVIDIA via NVENC (h264_nvenc → mkv). Requires nvidia runtime.
 */
export type VideoHwAccel = 'none' | 'vaapi' | 'nvenc';

/**
 * VideoRecordingService captures the Xvfb display using ffmpeg x11grab and
 * encodes to a video file. This works uniformly across Google Meet, Teams,
 * and Zoom Web since all three render into the same virtual display.
 *
 * Usage:
 *   const svc = new VideoRecordingService(meetingId, sessionUid);
 *   svc.start();
 *   // ... meeting runs ...
 *   await svc.stop();
 *   await svc.upload(uploadUrl, token);
 *   await svc.cleanup();
 */
export class VideoRecordingService {
  private filePath: string;
  private format: string;
  private ffmpegProcess: ChildProcess | null = null;
  private isRunning = false;
  private startTime = 0;
  private display: string;
  private hwaccel: VideoHwAccel;

  constructor(
    private meetingId: number,
    private sessionUid: string,
  ) {
    this.display = process.env.DISPLAY || ':99';
    this.hwaccel = (process.env.VIDEO_HWACCEL || 'none').toLowerCase() as VideoHwAccel;
    this.format = this.hwaccel === 'none' ? 'webm' : 'mkv';
    this.filePath = path.join('/tmp', `video_recording_${meetingId}_${sessionUid}.${this.format}`);
  }

  start(): void {
    if (this.isRunning) {
      log('[VideoRecording] Already running');
      return;
    }

    const args = this.buildFfmpegArgs();
    log(`[VideoRecording] Starting ffmpeg (hwaccel=${this.hwaccel}): ffmpeg ${args.join(' ')}`);

    this.ffmpegProcess = spawn('ffmpeg', args, {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    this.isRunning = true;
    this.startTime = Date.now();

    this.ffmpegProcess.stderr?.on('data', (data: Buffer) => {
      // ffmpeg writes progress to stderr; only log errors
      const text = data.toString().trim();
      if (text.includes('Error') || text.includes('error') || text.includes('failed')) {
        log(`[VideoRecording] ffmpeg: ${text}`);
      }
    });

    this.ffmpegProcess.on('exit', (code) => {
      this.isRunning = false;
      log(`[VideoRecording] ffmpeg exited with code ${code}`);
    });

    this.ffmpegProcess.on('error', (err) => {
      this.isRunning = false;
      log(`[VideoRecording] ffmpeg spawn error: ${err.message}`);
    });
  }

  /**
   * Stop recording. Sends SIGTERM to ffmpeg which finalizes the output file
   * before exiting. Returns the path to the recorded file.
   */
  stop(): Promise<string> {
    if (!this.ffmpegProcess || !this.isRunning) {
      return Promise.resolve(this.filePath);
    }

    return new Promise((resolve) => {
      const onExit = () => {
        clearTimeout(forceKillTimer);
        log('[VideoRecording] ffmpeg stopped gracefully');
        resolve(this.filePath);
      };

      this.ffmpegProcess!.once('exit', onExit);

      // SIGTERM tells ffmpeg to flush and finalize the output file
      this.ffmpegProcess!.kill('SIGTERM');

      // Force kill after 15 seconds if it doesn't exit cleanly
      const forceKillTimer = setTimeout(() => {
        log('[VideoRecording] ffmpeg did not exit in time, sending SIGKILL');
        this.ffmpegProcess?.kill('SIGKILL');
        resolve(this.filePath);
      }, 15000);
    });
  }

  /**
   * Upload the video file to the bot-manager upload endpoint.
   * Sends media_type: "video" so bot-manager stores it as a video MediaFile.
   */
  async upload(callbackUrl: string, token: string): Promise<void> {
    if (!fs.existsSync(this.filePath)) {
      log(`[VideoRecording] File not found for upload: ${this.filePath}`);
      return;
    }

    const fileData = await fs.promises.readFile(this.filePath);
    const fileStats = await fs.promises.stat(this.filePath);
    const durationSeconds = (Date.now() - this.startTime) / 1000;

    log(`[VideoRecording] Uploading ${fileStats.size} bytes (${durationSeconds.toFixed(1)}s) to ${callbackUrl}`);

    const boundary = `----VexaVideoRecording${Date.now()}`;
    const contentTypeMap: Record<string, string> = {
      webm: 'video/webm',
      mkv: 'video/x-matroska',
      mp4: 'video/mp4',
    };
    const fileContentType = contentTypeMap[this.format] || 'video/webm';

    const metadata = JSON.stringify({
      meeting_id: this.meetingId,
      session_uid: this.sessionUid,
      media_type: 'video',
      format: this.format,
      duration_seconds: durationSeconds,
      file_size_bytes: fileStats.size,
    });

    const parts: Buffer[] = [];
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n`));
    parts.push(Buffer.from(metadata));
    parts.push(Buffer.from('\r\n'));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="video.${this.format}"\r\nContent-Type: ${fileContentType}\r\n\r\n`));
    parts.push(fileData);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    return new Promise((resolve, reject) => {
      const url = new URL(callbackUrl);
      const transport = url.protocol === 'https:' ? https : http;
      const req = transport.request(
        {
          hostname: url.hostname,
          port: url.port,
          path: url.pathname,
          method: 'POST',
          headers: {
            'Content-Type': `multipart/form-data; boundary=${boundary}`,
            'Content-Length': body.length,
            'Authorization': `Bearer ${token}`,
          },
        },
        (res) => {
          let responseData = '';
          res.on('data', (chunk) => { responseData += chunk; });
          res.on('end', () => {
            if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
              log(`[VideoRecording] Upload successful: ${res.statusCode}`);
              resolve();
            } else {
              log(`[VideoRecording] Upload failed: ${res.statusCode} - ${responseData}`);
              reject(new Error(`Video upload failed with status ${res.statusCode}: ${responseData}`));
            }
          });
        }
      );
      req.on('error', (err) => {
        log(`[VideoRecording] Upload error: ${err.message}`);
        reject(err);
      });
      req.write(body);
      req.end();
    });
  }

  /**
   * Mux an audio file into the video, producing a self-contained file.
   * Copies the video stream as-is and encodes audio (opus for webm, aac for mkv/mp4).
   * Replaces this.filePath with the muxed output.
   */
  /**
   * @param audioPath   Path to the finalized WAV file.
   * @param audioDelayMs  Delay (in ms) to apply to the audio stream.
   *   Positive = audio started later than video, so we pad silence at the start.
   *   This keeps audio and video in sync when they didn't start at the same time.
   */
  async muxAudio(audioPath: string, audioDelayMs: number = 0): Promise<void> {
    if (!fs.existsSync(this.filePath)) {
      log(`[VideoRecording] Video file not found for muxing: ${this.filePath}`);
      return;
    }
    if (!fs.existsSync(audioPath)) {
      log(`[VideoRecording] Audio file not found for muxing: ${audioPath}`);
      return;
    }

    const muxedPath = this.filePath.replace(`.${this.format}`, `_muxed.${this.format}`);
    const audioCodec = this.format === 'webm' ? 'libopus' : 'aac';
    const audioDelaySec = Math.max(0, audioDelayMs / 1000);

    // -itsoffset delays the audio input so it aligns with the video timeline.
    // Without this, audio that started later than video would play too early.
    const args = [
      '-y',
      '-i', this.filePath,
      ...(audioDelaySec > 0 ? ['-itsoffset', audioDelaySec.toFixed(3)] : []),
      '-i', audioPath,
      '-c:v', 'copy',
      '-c:a', audioCodec,
      '-shortest',
      muxedPath,
    ];

    log(`[VideoRecording] Muxing audio into video: ffmpeg ${args.join(' ')}`);

    return new Promise((resolve) => {
      const proc = spawn('ffmpeg', args, { stdio: ['ignore', 'pipe', 'pipe'] });
      let stderr = '';
      proc.stderr?.on('data', (data: Buffer) => { stderr += data.toString(); });
      proc.on('exit', async (code) => {
        if (code === 0 && fs.existsSync(muxedPath)) {
          // Replace the original video-only file with the muxed one
          try {
            await fs.promises.unlink(this.filePath);
          } catch {}
          this.filePath = muxedPath;
          const stats = fs.statSync(muxedPath);
          log(`[VideoRecording] Muxed file ready: ${muxedPath} (${stats.size} bytes)`);
        } else {
          log(`[VideoRecording] Mux failed (code=${code}): ${stderr.slice(-500)}`);
          // Keep the original video-only file for upload
        }
        resolve();
      });
      proc.on('error', (err) => {
        log(`[VideoRecording] Mux spawn error: ${err.message}`);
        resolve();
      });
    });
  }

  async cleanup(): Promise<void> {
    try {
      if (fs.existsSync(this.filePath)) {
        await fs.promises.unlink(this.filePath);
        log(`[VideoRecording] Cleaned up ${this.filePath}`);
      }
    } catch (err: any) {
      log(`[VideoRecording] Cleanup error: ${err.message}`);
    }
  }

  getFilePath(): string {
    return this.filePath;
  }

  getStartTime(): number {
    return this.startTime;
  }

  // ---------------------------------------------------------------------------

  private buildFfmpegArgs(): string[] {
    const fps = '10';
    const inputSize = '1920x1080';

    // Pre-input args (e.g. hwaccel flags that must appear before -i)
    let preInputArgs: string[] = [];
    let encoderArgs: string[];
    let outputFile: string;

    switch (this.hwaccel) {
      case 'vaapi': {
        // Intel iGPU / AMD Radeon via VA-API
        // Requires /dev/dri/renderD128 device in container
        encoderArgs = [
          '-vaapi_device', '/dev/dri/renderD128',
          '-vf', 'format=nv12,hwupload',
          '-c:v', 'h264_vaapi',
          '-qp', '28',
        ];
        outputFile = this.filePath; // .mkv
        break;
      }
      case 'nvenc': {
        // NVIDIA via NVENC — -hwaccel cuda must precede -i
        preInputArgs = ['-hwaccel', 'cuda'];
        encoderArgs = [
          '-c:v', 'h264_nvenc',
          '-cq', '28',
          '-preset', 'p2',
        ];
        outputFile = this.filePath; // .mkv
        break;
      }
      default: {
        // Software encoding with VP9 — excellent compression for screen content
        encoderArgs = [
          '-c:v', 'libvpx-vp9',
          '-crf', '35',
          '-b:v', '0',
          '-deadline', 'realtime',
          '-cpu-used', '8',
          '-row-mt', '1',
        ];
        outputFile = this.filePath; // .webm
        break;
      }
    }

    // Common input: x11grab from the virtual display
    const inputArgs = [
      '-f', 'x11grab',
      '-framerate', fps,
      '-video_size', inputSize,
      '-i', this.display,
    ];

    return [
      '-y',         // overwrite output file if exists
      ...preInputArgs,
      ...inputArgs,
      ...encoderArgs,
      '-an',        // no audio (audio is muxed in after recording stops)
      outputFile,
    ];
  }
}
