/**
 * retention — rolling N-day raw `capture.v1` retention on the live ingest path,
 * so a real production meeting can be **dumped** into a replayable fixture after
 * the fact (the "probe prod for fixtures" linchpin).
 *
 * One faithful `stream.capture` per meeting (via StreamCaptureWriter), keyed by
 * day + meeting under a retention root. A sweeper prunes day-dirs older than the
 * window; the `dump` CLI promotes one meeting into the fixture store. Both ingest
 * seams (bot in-process, ingest-server WS) open a writer through here, so there's
 * one layout and one format.
 *
 * ENV (all off by default → zero behaviour change until enabled):
 *   CAPTURE_RETENTION=1            turn retention on
 *   VEXA_CAPTURE_RETENTION_DIR     root (default ~/.vexa/retention)
 *   CAPTURE_RETENTION_DAYS=7       sweep window
 */
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { StreamCaptureWriter } from './stream-capture';
import { CaptureMeta } from './contracts/capture-v1';

export function retentionRoot(): string {
  return process.env.VEXA_CAPTURE_RETENTION_DIR || path.join(os.homedir(), '.vexa', 'retention');
}

export function retentionEnabled(): boolean {
  return process.env.CAPTURE_RETENTION === '1' || process.env.CAPTURE_RETENTION === 'true';
}

export function retentionDays(): number {
  const n = Number(process.env.CAPTURE_RETENTION_DAYS || 7);
  return Number.isFinite(n) && n > 0 ? n : 7;
}

function dayStamp(d = new Date()): string {
  return d.toISOString().slice(0, 10); // YYYY-MM-DD — sweepable by name
}

function slug(s: string | number): string {
  return String(s).replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 80);
}

export interface RetentionKey extends CaptureMeta {
  /** The Vexa internal meeting_id (preferred dump key). */
  meetingId?: string | number;
  /** Fallback when no meeting_id resolved (e.g. the ext connection id). */
  connectionId?: string;
}

/**
 * Open a faithful `stream.capture` writer under the rolling retention root,
 * keyed by day + meeting. Returns null when retention is disabled — callers
 * write `writer?.rawAudio(...)` so the tee is a no-op until CAPTURE_RETENTION=1.
 * The caller MUST `await writer.finalize()` on disconnect.
 */
export function openRetentionWriter(key: RetentionKey): StreamCaptureWriter | null {
  if (!retentionEnabled()) return null;
  const name = [key.platform || 'unknown', key.nativeMeetingId ?? '?', key.meetingId ?? key.connectionId ?? 'x']
    .map(slug).join('-');
  const dir = path.join(retentionRoot(), dayStamp(), name);
  try {
    return new StreamCaptureWriter(dir, key);
  } catch {
    return null; // retention must never break a live session
  }
}

/** Delete retention day-dirs older than the window (by the YYYY-MM-DD dir name). */
export function sweepRetention(days = retentionDays()): { kept: string[]; removed: string[] } {
  const root = retentionRoot();
  const removed: string[] = [], kept: string[] = [];
  if (!fs.existsSync(root)) return { kept, removed };
  const cutoff = Date.now() - days * 86400_000;
  for (const day of fs.readdirSync(root)) {
    const t = Date.parse(day);
    const full = path.join(root, day);
    if (!isNaN(t) && t < cutoff) { fs.rmSync(full, { recursive: true, force: true }); removed.push(day); }
    else kept.push(day);
  }
  return { kept, removed };
}

export interface RetainedMeeting { day: string; name: string; dir: string; bytes: number }

/** All retained meetings, newest day first. */
export function listRetention(): RetainedMeeting[] {
  const root = retentionRoot();
  const out: RetainedMeeting[] = [];
  if (!fs.existsSync(root)) return out;
  for (const day of fs.readdirSync(root).sort().reverse()) {
    const dayDir = path.join(root, day);
    if (!fs.statSync(dayDir).isDirectory()) continue;
    for (const name of fs.readdirSync(dayDir)) {
      const dir = path.join(dayDir, name);
      const cap = path.join(dir, 'stream.capture');
      out.push({ day, name, dir, bytes: fs.existsSync(cap) ? fs.statSync(cap).size : 0 });
    }
  }
  return out;
}

/** Find one retained meeting by substring of its name (or exact dir). */
export function findRetained(query: string): RetainedMeeting | null {
  const all = listRetention();
  return all.find(m => m.name === query || m.dir === query) // exact first
      || all.find(m => m.name.includes(query)) || null;     // then substring
}
