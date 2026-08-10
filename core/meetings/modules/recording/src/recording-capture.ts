/**
 * RecordingCaptureService — the ffmpeg-for-all recording capture.
 *
 * ONE ffmpeg process produces live, chunked HLS (fMP4/CMAF) directly, for BOTH modes:
 *   - audio-only (the default): `-f pulse -i record_sink.monitor` → AAC/Opus fMP4 segments
 *   - audio+video: add `-f x11grab` of the Xvfb display → muxed H.264(+…)/AAC fMP4 segments (one clock →
 *     perfect lip-sync, no server-side offset)
 *
 * HLS (fMP4) is the packaging because it plays NATIVELY in Safari + iOS (no MSE, low battery) and via
 * hls.js everywhere else — chunked/adaptive delivery for smooth mobile streaming. ffmpeg writes
 * `init.mp4` + `chunk-<NNNNN>.m4s` + a `playlist.m3u8` (ENDLIST on clean finalize → VOD). A watcher emits
 * each file via `onFile(relpath, absPath)` as it becomes safe to upload; the bot uploads them under the
 * recording's `.../hls/` prefix verbatim, so the playlist's relative URIs resolve with no rewrite.
 *
 * Codecs are configurable (RECORD_VIDEO_CODEC / RECORD_AUDIO_CODEC) and compose with VIDEO_HWACCEL into
 * an encoder matrix; an impossible combo (e.g. NVENC + VP9) fails LOUD at start rather than silently.
 * Defaults are h264 + aac — the only combo that plays in EVERY browser incl. native Safari/iOS.
 *
 * The meeting audio reaches ffmpeg because setup-pulseaudio-sinks.sh makes `record_sink` (48 kHz,
 * unmuted) the default output, so Chromium renders there; `record_sink.monitor` is the capture source.
 */
import { spawn, ChildProcess } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { log } from './log';

export interface RecordingCaptureOptions {
  meetingId: number;
  sessionUid: string;
  /** false (default) = audio-only; true = capture the Xvfb display too. */
  withVideo?: boolean;
  /** Fires once per fully-written segment/init, and on the playlist (re-upload). */
  onFile?: (relpath: string, absPath: string) => void;
}

type VideoCodec = 'h264' | 'hevc' | 'vp9' | 'av1';
type AudioCodec = 'aac' | 'opus';

export class RecordingCaptureService {
  private proc: ChildProcess | null = null;
  private running = false;
  private startTime = 0;
  private readonly meetingId: number;
  private readonly sessionUid: string;
  private readonly withVideo: boolean;
  private readonly hlsDir: string;
  private readonly playlistPath: string;
  private readonly segTime: string;
  private readonly display: string;
  private readonly audioSource: string;
  private readonly videoSize: string;
  private readonly fps: string;
  private readonly hwaccel: string;
  private readonly videoCodec: VideoCodec;
  private readonly audioCodec: AudioCodec;
  private watcher: fs.FSWatcher | null = null;
  private emitted = new Set<string>();          // dedup for segments/init (the playlist is NEVER deduped)

  /** Set by the bot to upload each HLS file. */
  public onFile: RecordingCaptureOptions['onFile'] = undefined;

  constructor(opts: RecordingCaptureOptions) {
    this.meetingId = opts.meetingId;
    this.sessionUid = opts.sessionUid;
    this.withVideo = !!opts.withVideo;
    this.onFile = opts.onFile;
    this.segTime = process.env.RECORD_SEGMENT_SECONDS || '15';
    this.display = process.env.DISPLAY || ':99';
    this.audioSource = process.env.RECORD_AUDIO_SOURCE || 'record_sink.monitor';
    // VIDEO_RESOLUTION drives the bot's Xvfb + Chromium too — x11grab MUST match it.
    this.videoSize = process.env.VIDEO_RESOLUTION || '1920x1080';
    this.fps = '10';
    this.hwaccel = (process.env.VIDEO_HWACCEL || 'none').toLowerCase();
    this.videoCodec = (process.env.RECORD_VIDEO_CODEC || 'h264').toLowerCase() as VideoCodec;
    this.audioCodec = (process.env.RECORD_AUDIO_CODEC || 'aac').toLowerCase() as AudioCodec;
    this.hlsDir = path.join('/tmp', `hls_${this.meetingId}_${this.sessionUid}`);
    this.playlistPath = path.join(this.hlsDir, 'playlist.m3u8');
  }

  start(): void {
    if (this.running) { log('[RecordingCapture] already running'); return; }
    try { fs.mkdirSync(this.hlsDir, { recursive: true }); } catch { /* exists */ }
    const args = this.buildArgs(); // throws on an impossible codec/hwaccel combo (fail LOUD)
    log(`[RecordingCapture] starting ffmpeg (video=${this.withVideo}, ${this.videoCodec}/${this.audioCodec}, hwaccel=${this.hwaccel}): ffmpeg ${args.join(' ')}`);
    this.proc = spawn('ffmpeg', args, { stdio: ['ignore', 'pipe', 'pipe'] });
    this.running = true;
    this.startTime = Date.now();
    this.proc.stderr?.on('data', (d: Buffer) => {
      const t = d.toString().trim();
      if (/error|failed/i.test(t)) log(`[RecordingCapture] ffmpeg: ${t}`);
    });
    this.proc.on('exit', (code) => { this.running = false; log(`[RecordingCapture] ffmpeg exited ${code}`); });
    this.proc.on('error', (e) => { this.running = false; log(`[RecordingCapture] ffmpeg spawn error: ${e.message}`); });
    this.startWatcher();
  }

  /** SIGTERM → ffmpeg flushes the final segment + writes #EXT-X-ENDLIST; then flush anything missed. */
  stop(): Promise<void> {
    if (!this.proc || !this.running) { this.finalize(); return Promise.resolve(); }
    return new Promise((resolve) => {
      const done = () => { clearTimeout(kill); this.finalize(); resolve(); };
      this.proc!.once('exit', done);
      this.proc!.kill('SIGTERM');
      const kill = setTimeout(() => { this.proc?.kill('SIGKILL'); this.finalize(); resolve(); }, 15000);
    });
  }

  async cleanup(): Promise<void> {
    try { await fs.promises.rm(this.hlsDir, { recursive: true, force: true }); } catch { /* best-effort */ }
  }

  getStartTime(): number { return this.startTime; }
  getPlaylistPath(): string { return this.playlistPath; }
  isWithVideo(): boolean { return this.withVideo; }

  // ── ffmpeg args ─────────────────────────────────────────────────────────────

  /** The encoder for (RECORD_VIDEO_CODEC × VIDEO_HWACCEL). `null` = an impossible combo → fail LOUD so a
   *  mis-set deployment never silently produces something (e.g. NVENC has no VP9 encoder). */
  private videoEncoderArgs(): string[] {
    const MATRIX: Record<VideoCodec, Record<string, string[] | null>> = {
      h264: {
        none:  ['-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-crf', '28', '-pix_fmt', 'yuv420p'],
        nvenc: ['-c:v', 'h264_nvenc', '-preset', 'p2', '-cq', '28', '-pix_fmt', 'yuv420p'],
        vaapi: ['-vf', 'format=nv12,hwupload', '-c:v', 'h264_vaapi', '-qp', '28'],
      },
      hevc: {
        // hvc1 tag is REQUIRED for Apple/Safari HLS playback (hev1 won't play natively).
        none:  ['-c:v', 'libx265', '-preset', 'ultrafast', '-crf', '28', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1'],
        nvenc: ['-c:v', 'hevc_nvenc', '-preset', 'p2', '-cq', '28', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1'],
        vaapi: ['-vf', 'format=nv12,hwupload', '-c:v', 'hevc_vaapi', '-qp', '28', '-tag:v', 'hvc1'],
      },
      vp9: {
        none:  ['-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '32', '-deadline', 'realtime', '-cpu-used', '8', '-pix_fmt', 'yuv420p'],
        nvenc: null, // NVENC has no VP9 encoder
        vaapi: ['-vf', 'format=nv12,hwupload', '-c:v', 'vp9_vaapi', '-qp', '32'],
      },
      av1: {
        none:  ['-c:v', 'libsvtav1', '-preset', '10', '-crf', '32', '-pix_fmt', 'yuv420p'],
        nvenc: ['-c:v', 'av1_nvenc', '-preset', 'p2', '-cq', '32', '-pix_fmt', 'yuv420p'], // RTX 40-series+
        vaapi: ['-vf', 'format=nv12,hwupload', '-c:v', 'av1_vaapi', '-qp', '32'],           // Intel Arc+
      },
    };
    const byHw = MATRIX[this.videoCodec];
    if (!byHw) throw new Error(`RECORD_VIDEO_CODEC="${this.videoCodec}" not supported (h264|hevc|vp9|av1)`);
    const args = byHw[this.hwaccel];
    if (args === null) throw new Error(`VIDEO_HWACCEL="${this.hwaccel}" cannot encode ${this.videoCodec} (e.g. NVENC has no VP9) — pick a supported codec/hwaccel combo`);
    if (!args) throw new Error(`VIDEO_HWACCEL="${this.hwaccel}" unknown (none|nvenc|vaapi)`);
    return args;
  }

  private audioEncoderArgs(): string[] {
    switch (this.audioCodec) {
      case 'aac':  return ['-c:a', 'aac', '-b:a', '128k'];
      case 'opus': return ['-c:a', 'libopus', '-b:a', '128k'];
      default: throw new Error(`RECORD_AUDIO_CODEC="${this.audioCodec}" not supported (aac|opus)`);
    }
  }

  private hlsOutputArgs(): string[] {
    return [
      '-f', 'hls',
      '-hls_time', this.segTime,
      '-hls_list_size', '0',            // keep EVERY segment in the playlist (VOD, no sliding window)
      '-hls_playlist_type', 'vod',      // writes #EXT-X-ENDLIST on clean finalize → seekable VOD
      '-hls_segment_type', 'fmp4',      // CMAF fMP4 segments (native Safari + hls.js)
      '-hls_flags', 'independent_segments',
      '-hls_fmp4_init_filename', 'init.mp4',
      '-hls_segment_filename', path.join(this.hlsDir, 'chunk-%05d.m4s'),
      this.playlistPath,
    ];
  }

  private buildArgs(): string[] {
    const audioInput = ['-f', 'pulse', '-i', this.audioSource];
    if (!this.withVideo) {
      return ['-y', ...audioInput, ...this.audioEncoderArgs(), ...this.hlsOutputArgs()];
    }
    const preInput = this.hwaccel === 'nvenc' ? ['-hwaccel', 'cuda'] : [];
    const videoInput = ['-f', 'x11grab', '-draw_mouse', '0', '-framerate', this.fps, '-video_size', this.videoSize, '-i', this.display];
    return [
      '-y',
      ...preInput,
      ...videoInput,
      ...audioInput,
      '-map', '0:v:0', '-map', '1:a:0',
      ...this.videoEncoderArgs(),
      ...this.audioEncoderArgs(),
      // Force keyframes on segment boundaries so each HLS segment is independently decodable.
      '-force_key_frames', `expr:gte(t,n_forced*${this.segTime})`,
      ...this.hlsOutputArgs(),
    ];
  }

  // ── watcher ─────────────────────────────────────────────────────────────────

  private startWatcher(): void {
    try {
      this.watcher = fs.watch(this.hlsDir, (_e, filename) => {
        if (!filename) return;
        const name = filename.toString();
        if (name === 'playlist.m3u8') { this.emit('playlist.m3u8', true); return; } // re-emit on EVERY change
        if (name === 'init.mp4') { this.emit('init.mp4', false); return; }
        const m = /^chunk-(\d+)\.m4s$/.exec(name);
        if (!m) return;
        const n = parseInt(m[1], 10);
        // Segment N appearing means segment N-1 is fully written (ffmpeg opened the next).
        if (n > 0) this.emit(`chunk-${String(n - 1).padStart(5, '0')}.m4s`, false);
      });
    } catch (e) {
      log(`[RecordingCapture] watcher failed: ${String(e)}`);
    }
  }

  /** Emit a file for upload. `always` (the playlist) skips the dedup + size guard so live updates flow. */
  private emit(relpath: string, always: boolean): void {
    if (!always && this.emitted.has(relpath)) return;
    const abs = path.join(this.hlsDir, relpath);
    let size = 0;
    try { size = fs.statSync(abs).size; } catch { return; } // not written yet
    if (!always && size === 0) return;                       // created but empty — a later trigger re-emits
    if (!always) this.emitted.add(relpath);
    try { this.onFile?.(relpath, abs); } catch (e) { log(`[RecordingCapture] onFile error: ${String(e)}`); }
  }

  /** On stop, flush every file the watcher may not have emitted (the last segment, init, and the final
   *  playlist with its ENDLIST), then close the watcher. Idempotent (emit dedups). */
  private finalize(): void {
    try { this.watcher?.close(); } catch { /* noop */ }
    this.watcher = null;
    try {
      for (const f of fs.readdirSync(this.hlsDir)) {
        if (f === 'playlist.m3u8') this.emit(f, true);
        else this.emit(f, false);
      }
    } catch { /* dir gone */ }
  }
}
