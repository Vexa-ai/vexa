import type { ComponentType } from "react";

export interface TranscriptSegment {
  speaker?: string;
  text: string;
  ts?: number | string;
}

export type EntityKind = "person" | "company" | "product" | "number";

export interface CanvasEntity {
  kind: EntityKind;
  title: string;
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

export interface MeetingState {
  meeting: {
    id: string;
    title: string;
    startedAt?: string;
    participants?: string[];
  };
  transcript: {
    segments: TranscriptSegment[];
    liveCaption?: string;
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
