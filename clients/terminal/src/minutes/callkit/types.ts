/** The call kit's vocabulary — what a call SHOWS, independent of the media stack that feeds it.
 *  Every component in this folder is presentational: props in, callbacks out, no fetch, no LiveKit.
 *  The LiveKit room (CallRoom) and the UI lab both map their state onto these shapes. */
import type { ReactNode } from "react";

export interface CallPerson {
  id: string;
  name: string;
  /** two letters when there is no image */
  initials?: string;
  image?: string;
  /** this browser's own participant */
  self?: boolean;
  /** Vexa — the agent in the call (listens, and speaks when addressed) */
  agent?: boolean;
  speaking?: boolean;
  muted?: boolean;
  camera?: boolean;
  /** the camera feed itself (a <video> in the product); shown when `camera` is on */
  feed?: ReactNode;
  /** still connecting */
  joining?: boolean;
}

/** What Vexa is doing in the call right now. */
export type AgentState = "listening" | "thinking" | "speaking" | "paused";

/** A piece of an agent's answer: SPOKEN aloud in the call, or SHOWN only (tables, links, ids). */
export interface CallLinePart { text: string; mode: "spoken" | "shown" }

export interface CallLine {
  id: string;
  /** participant id */
  who: string;
  name: string;
  /** seconds from the start of the call */
  at: number;
  text?: string;
  /** an agent answer, split into what was said and what was only shown */
  parts?: CallLinePart[];
  agent?: boolean;
  /** still being spoken (the line grows) */
  partial?: boolean;
}

export interface ScreenShare {
  by: string;
  byName: string;
  /** what is shared: "Window · AIT Lisbon budget" */
  title: string;
  content: ReactNode;
}

export interface Device { id: string; label: string }
export interface Devices {
  mic: Device[]; cam: Device[]; speaker: Device[];
  selected: { mic?: string; cam?: string; speaker?: string };
}

export interface CallControlsState {
  mic: boolean;
  cam: boolean;
  sharing: boolean;
  transcript: boolean;
}

/** m:ss or h:mm:ss */
export function clock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

export function initialsOf(name: string): string {
  const w = name.trim().split(/[\s._-]+/).filter(Boolean);
  return ((w[0]?.[0] ?? "") + (w[1]?.[0] ?? "")).toUpperCase() || "?";
}
