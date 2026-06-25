import type { ComponentType } from "react";

export interface MeetingState {
  meeting: {
    id: string;
    title: string;
    startedAt?: string;
    participants?: string[];
  };
  transcript: {
    segments: { speaker?: string; text: string; ts?: number | string }[];
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
