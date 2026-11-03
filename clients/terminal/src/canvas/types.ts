import type { ComponentType } from "react";

/** A stretch of captured audio that was never transcribed (prod-cutover R4) — drawn as a quiet
 *  marker where the words would have been, never as a segment. */
export interface TranscriptGap {
  startMs?: number;           // ABSOLUTE wall-clock start, epoch ms
  endMs?: number;
  label: string;              // "gap mm:ss–mm:ss (not transcribed)", composed by meeting-api
}

export interface TranscriptSegment {
  id?: string;
  speaker?: string;
  text: string;
  ts?: number | string;       // meeting-relative time (seconds) — back-compat
  endMs?: number;
  tsMs?: number;              // ABSOLUTE wall-clock time of the line, epoch ms (UTC). Renderer formats in local TZ.
  completed?: boolean;        // false = live pending (in-progress ASR); true/undefined = finalized
}

export type EntityKind = "person" | "company" | "product" | "number" | "signal";

export interface CanvasEntity {
  kind: EntityKind;
  title: string;
  subtitle?: string;
  body?: string;
  value?: number | string;
}

export interface EntityItem {
  id: string;
  kind: EntityKind;
  name: string;
  context?: string;
  summary?: string;
  quote?: string;
  docPath?: string;
  researched?: boolean;
  title?: string;
  subtitle?: string;
  body?: string;
  value?: number | string;
}

export interface SpeakerSummary {
  name: string;
  segments: number;
  talkMs: number;
  talkPct: number;
}

export interface MeetingDocLink {
  path: string;
  present: boolean;
  title?: string;
}

/** A fault in the meeting FEED. There is no "model" kind: the product runs no model calls of its
 *  own beside the agent (PRD decision 34), so there is no inference to fail here. */
export interface MeetingDiagnosticIssue {
  kind: "stream" | "parse" | "access";
  message: string;
  status?: number;
  at?: number;
}

export interface MeetingState {
  meeting: {
    id: string;
    nativeId?: string;
    platform?: string;          // "voice_note" picks the note's own words over the bot's
    title: string;
    status?: string;
    live?: boolean;             // true while the backend session is live (session_uid present)
    startedAt?: string;
    participants?: string[];
    docs?: { path: string; title?: string; kind?: string; present?: boolean }[];
    deleted?: boolean;          // its owner deleted the transcript + recordings; nothing is shown
  };
  transcript: {
    segments: TranscriptSegment[];
    liveCaption?: string;
    /** Audio the meeting never transcribed — recorded meetings only. */
    gaps?: TranscriptGap[];
  };
  entities: {
    people: any[];
    companies: any[];
    products: any[];
    numbers: any[];
  };
  cards: { id: string; kind: string; title: string; body?: string; ts?: number | string }[];
  diagnostics?: {
    liveConnected?: boolean;
    ended?: boolean;
    reconnects?: number;
    lastEventAt?: number;
    lastTranscriptAt?: number;
    /** The bot's latest transcription progress report (seconds behind, seconds quiet, final pass). */
    progress?: { backlogS: number; silenceS: number | null; finishing: boolean; at: number };
    issues?: MeetingDiagnosticIssue[];
  };
  metrics: Record<string, number | string>;
  sections: Record<string, unknown>;
}

export interface HarnessActions {
  ask(prompt: string): void;
  research(entity: { name: string; kind: string }): void;
  openDoc(path: string): void;
  copyRef(token: string): void;
  note(text: string): void;
  pin(id: string): void;
  dismiss(id: string): void;
  setMetric(key: string, value: number | string): void;
  tag(speaker: string, label: string): void;
  export(): void;
}

export type ViewModule = {
  default: ComponentType;
};
