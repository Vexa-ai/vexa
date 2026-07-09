#!/usr/bin/env node
// flag-store — the O-TEL-3 flag→store→surface→replay loop, file/in-memory (the eval needs no DB).
//
// turns a flagged transcript bug into a routable, reproducible test:
//   flag(issue)        — the "POST /issues/flag" entry (user AND system); stamps id/created_at,
//                        normalizes status to 'open', appends to the store (file-backed if FLAG_STORE
//                        is set, else pure in-memory). Returns the stored flagged-issue.v1 record.
//   queue({open})      — the SYSTEM QUEUE: the surfaced list (open/investigating first), newest last.
//   get(issue_id)      — fetch one stored issue.
//   routeToReplay(id)  — resolve an issue's signal link → the EXACT O-TEL-2 replay command, so a
//                        flagged bug routes straight to its deterministic offline repro.
//
// The store is a tiny class so the eval (flag.test.ts) drives it directly; an HTTP adapter
// (the real "POST /issues/flag" + "GET /issues") would wrap the same methods at the seam.
import { readFileSync, writeFileSync, existsSync } from 'node:fs';

let counter = 0;
const nowIso = () => new Date().toISOString();
const newId = () => `issue-${Date.now().toString(36)}-${(++counter).toString(36)}`;

export class FlagStore {
  /** @param {string|null} file - optional JSON file to persist to (file-backed); null = in-memory. */
  constructor(file = process.env.FLAG_STORE || null) {
    this.file = file;
    this.issues = [];
    if (this.file && existsSync(this.file)) {
      try { this.issues = JSON.parse(readFileSync(this.file, 'utf8')); } catch { this.issues = []; }
    }
  }

  _persist() {
    if (this.file) { try { writeFileSync(this.file, JSON.stringify(this.issues, null, 2) + '\n'); } catch { /* best-effort */ } }
  }

  /**
   * Flag an issue (the "POST /issues/flag"). Accepts a partial flagged-issue.v1 record; fills
   * issue_id / created_at / status defaults. Whether `flagged_by` is 'human', 'feedback-api', or
   * 'system' (auto-flag), it lands in the SAME store + queue. Returns the stored record.
   */
  flag(issue) {
    const record = {
      ...issue,                                       // caller fields win
      issue_id: issue.issue_id || newId(),            // fill the id/created_at/status/flagged_by gaps
      status: issue.status || 'open',
      flagged_by: issue.flagged_by || 'human',
      created_at: issue.created_at || nowIso(),
    };
    this.issues.push(record);
    this._persist();
    return record;
  }

  get(issueId) { return this.issues.find((i) => i.issue_id === issueId) || null; }

  /** The system queue — surfaced issues. {open:true} shows only actionable (open/investigating). */
  queue({ open = false } = {}) {
    const live = open ? this.issues.filter((i) => i.status === 'open' || i.status === 'investigating') : this.issues.slice();
    // open/investigating bubble up; within a status, newest last (stable on created_at).
    const rank = (s) => (s === 'open' ? 0 : s === 'investigating' ? 1 : s === 'fixed' ? 2 : 3);
    return live.slice().sort((a, b) => rank(a.status) - rank(b.status) || (a.created_at < b.created_at ? -1 : 1));
  }

  all() { return this.issues.slice(); }

  /**
   * Route a flagged issue → its O-TEL-2 replay. Resolves the signal link (captured-signal.v1 or
   * legacy tape) into the EXACT replay command, so a flagged bug goes straight to its deterministic
   * offline repro. Returns { signalPath, replayCmd, frameRange? } or null if the issue has no signal.
   */
  routeToReplay(issueId) {
    const i = this.get(issueId);
    if (!i || !i.signal) return null;
    const signalPath = i.signal.captured_signal || i.signal.tape;
    if (!signalPath) return null;
    const frameRange = (i.signal.frame_seq_start != null && i.signal.frame_seq_end != null)
      ? { start: i.signal.frame_seq_start, end: i.signal.frame_seq_end } : null;
    return {
      issue_id: i.issue_id,
      signalPath,
      kind: i.signal.captured_signal ? 'captured-signal.v1' : 'tape',
      frameRange,
      // The live (server) repro: re-send the stored signal into a desktop ingest.
      replayCmd: `node eval/src/replay.mjs ${signalPath}`,
      // The offline (CI) repro: the deterministic gate:replay harness.
      offlineReplayCmd: `pnpm --filter @vexa/bot run replay`,
    };
  }
}

export const createFlagStore = (file) => new FlagStore(file);
