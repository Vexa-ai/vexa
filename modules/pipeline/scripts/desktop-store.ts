/**
 * desktop-store — the all-Node control plane for Vexa Desktop, on node:sqlite.
 *
 * Replaces Postgres + the Python meeting-api's client-facing surface with one
 * embedded file DB (zero deps, cross-platform). Holds meetings · sessions ·
 * confirmed segments. The pipeline writes confirmed segments here; the dashboard
 * reads /transcripts + /bots from here; the ingest resolves a session here.
 *
 * Live segments do NOT go through the DB — they broadcast straight out the WS
 * (no Redis). Only CONFIRMED history is persisted.
 *
 * (Runtime Node code — Date.now()/new Date() are fine here.)
 */
import { DatabaseSync } from 'node:sqlite';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';

export interface SegmentRow {
  segment_id: string; speaker: string; text: string;
  start: number; end?: number; language?: string | null; absolute_start_time: string;
}

export function openStore(dbPath = process.env.VEXA_DESKTOP_DB || path.join(os.homedir(), '.vexa', 'desktop.db')) {
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  return new Store(new DatabaseSync(dbPath), dbPath);
}

export class Store {
  constructor(private db: DatabaseSync, readonly path: string) {
    db.exec(`
      CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL, native_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        start_time TEXT, end_time TEXT, data TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER NOT NULL, session_uid TEXT NOT NULL,
        session_start_time TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE TABLE IF NOT EXISTS segments (
        meeting_id INTEGER NOT NULL,
        platform TEXT NOT NULL, native_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        speaker TEXT, text TEXT, start REAL, "end" REAL, language TEXT,
        absolute_start_time TEXT, completed INTEGER DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (meeting_id, segment_id)
      );
      CREATE INDEX IF NOT EXISTS ix_seg_meeting_start ON segments(meeting_id, start);
    `);
  }

  /** POST /extension/sessions — find the live meeting for (platform, native) or create it; open a session. */
  resolveSession(platform: string, nativeId: string): { meeting_id: number; session_uid: string } {
    const live = this.db.prepare(
      `SELECT id FROM meetings WHERE platform=? AND native_id=? AND status='active' ORDER BY id DESC LIMIT 1`
    ).get(platform, nativeId) as { id: number } | undefined;
    const id = live ? Number(live.id)
      : Number(this.db.prepare(`INSERT INTO meetings (platform, native_id, status, start_time) VALUES (?,?,'active',datetime('now'))`).run(platform, nativeId).lastInsertRowid);
    const session_uid = `ext-${platform}-${nativeId}-${id}-${Date.now()}`;
    this.db.prepare(`INSERT INTO sessions (meeting_id, session_uid) VALUES (?,?)`).run(id, session_uid);
    return { meeting_id: id, session_uid };
  }

  /** POST /extension/sessions/end — mark the meeting completed. */
  endMeeting(meetingId: number) {
    this.db.prepare(`UPDATE meetings SET status='completed', end_time=datetime('now') WHERE id=? AND status='active'`).run(meetingId);
  }

  /** GET /bots/id/{id} — status the ingest status-watch + dashboard read. */
  getMeeting(meetingId: number) {
    return this.db.prepare(`SELECT * FROM meetings WHERE id=?`).get(meetingId) || null;
  }

  /** GET /bots — recent meetings for the dashboard. */
  listMeetings(limit = 100) {
    return this.db.prepare(`SELECT * FROM meetings ORDER BY id DESC LIMIT ?`).all(limit);
  }

  /** Persist a CONFIRMED segment (idempotent on segment_id). */
  addSegment(meetingId: number, platform: string, nativeId: string, s: SegmentRow) {
    this.db.prepare(
      `INSERT INTO segments (meeting_id, platform, native_id, segment_id, speaker, text, start, "end", language, absolute_start_time, completed)
       VALUES (?,?,?,?,?,?,?,?,?,?,1)
       ON CONFLICT(meeting_id, segment_id) DO UPDATE SET speaker=excluded.speaker, text=excluded.text, "end"=excluded."end"`
    ).run(meetingId, platform, nativeId, s.segment_id, s.speaker, s.text, s.start, s.end ?? s.start, s.language ?? null, s.absolute_start_time);
  }

  /** GET /transcripts/{platform}/{native} — confirmed history, ordered. */
  getTranscripts(platform: string, nativeId: string) {
    return this.db.prepare(
      `SELECT segment_id, speaker, text, start, "end" AS end, absolute_start_time, completed
       FROM segments WHERE platform=? AND native_id=? ORDER BY start`
    ).all(platform, nativeId);
  }
}
