import type { ComponentType } from "react";

export interface TranscriptSegment {
  id?: string;
  speaker?: string;
  text: string;
  ts?: number | string;
}

export interface ProcessedTranscriptNote {
  id: string;
  speaker?: string;
  chapter?: string;
  text: string;
  ts?: number | string;
  pass?: number;
  frozen?: boolean;
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

export interface MeetingState {
  meeting: {
    id: string;
    nativeId?: string;
    title: string;
    status?: string;
    startedAt?: string;
    participants?: string[];
    docs?: { path: string; title?: string; kind?: string; present?: boolean }[];
  };
  transcript: {
    segments: TranscriptSegment[];
    liveCaption?: string;
    notes?: ProcessedTranscriptNote[];
  };
  entities: {
    people: any[];
    companies: any[];
    products: any[];
    numbers: any[];
  };
  cards: { id: string; kind: string; title: string; body?: string; ts?: number | string }[];
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
