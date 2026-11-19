"use client";
/** TRANSCRIPT SIGNALS for one meeting, live (ADR transcript signals, part C).
 *
 *  The signals service scores each transcript line against the meeting's listening brief and writes
 *  the result to `sig:meeting:{row}`; the gateway sends it on the shared `/ws` socket as ws.v1
 *  `meeting.signals` frames to a subscription that asked for `streams: ["signals"]` — granted by the
 *  same access check as the transcript, so a person sees a meeting's signals exactly when they may
 *  read its transcript. This module keeps them per meeting, keyed by the transcript segment they
 *  annotate, and hands them to the transcript renderer. No signals → an empty map, and the transcript
 *  renders as it always has. */
import { useEffect, useSyncExternalStore } from "react";
import { subscribeMeetingStreams } from "./gatewayWS";

export interface SignalAnnotation {
  /** the stream entry id — the newer entry for a segment wins */
  id: string;
  segment_id: string;
  /** `{label: 0..1}` against the brief */
  scores: Record<string, number>;
  /** `{word: significance 0..1}` */
  words: Record<string, number>;
  model?: string;
  brief_version?: number;
  at?: number;
}
export interface SignalsFrame {
  type: "meeting.signals";
  meeting: { id: string; platform: string; native_id: string };
  annotations: SignalAnnotation[];
}
export type AnnotationsBySegment = Record<string, SignalAnnotation>;

function numbers(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (!v || typeof v !== "object") return out;
  for (const [k, n] of Object.entries(v as Record<string, unknown>)) {
    if (typeof n === "number" && Number.isFinite(n)) out[k] = Math.max(0, Math.min(1, n));
  }
  return out;
}

/** One `meeting.signals` frame, or null. Annotations without an id or a segment are dropped. */
export function parseSignalsFrame(data: unknown): SignalsFrame | null {
  const o = data as Record<string, unknown> | null;
  if (!o || typeof o !== "object" || o.type !== "meeting.signals") return null;
  const m = o.meeting as Record<string, unknown> | undefined;
  if (!m || typeof m !== "object") return null;
  const annotations: SignalAnnotation[] = [];
  for (const raw of Array.isArray(o.annotations) ? o.annotations : []) {
    const a = raw as Record<string, unknown>;
    if (!a || typeof a !== "object" || typeof a.id !== "string" || typeof a.segment_id !== "string" || !a.segment_id) continue;
    annotations.push({
      id: a.id, segment_id: a.segment_id, scores: numbers(a.scores), words: numbers(a.words),
      ...(typeof a.model === "string" ? { model: a.model } : {}),
      ...(typeof a.brief_version === "number" ? { brief_version: a.brief_version } : {}),
      ...(typeof a.at === "number" ? { at: a.at } : {}),
    });
  }
  return {
    type: "meeting.signals",
    meeting: { id: String(m.id ?? ""), platform: String(m.platform ?? ""), native_id: String(m.native_id ?? "") },
    annotations,
  };
}

/** Redis stream ids compare as `<ms>-<seq>`. */
function newer(a: string, b: string): boolean {
  const [am, as] = a.split("-").map(Number), [bm, bs] = b.split("-").map(Number);
  return am !== bm ? am > bm : (as || 0) > (bs || 0);
}

/** Fold annotations into a by-segment map; a segment keeps its newest entry. Returns a new map. */
export function mergeAnnotations(into: AnnotationsBySegment, list: SignalAnnotation[]): AnnotationsBySegment {
  const out = { ...into };
  for (const a of list) {
    const cur = out[a.segment_id];
    if (!cur || newer(a.id, cur.id)) out[a.segment_id] = a;
  }
  return out;
}

/** The api platform a meeting row carries under its display name (`Google Meet` → `google_meet`). */
export function apiPlatform(platform?: string): string {
  return platform === "Google Meet" ? "google_meet" : String(platform ?? "");
}

interface Store { value: AnnotationsBySegment; subs: Set<() => void>; off?: () => void; refs: number }
const stores = new Map<string, Store>();
const EMPTY: AnnotationsBySegment = {};

function storeFor(key: string): Store {
  let s = stores.get(key);
  if (!s) { s = { value: EMPTY, subs: new Set(), refs: 0 }; stores.set(key, s); }
  return s;
}

/** The meeting's annotations by segment id, live. `enabled` false (signals off, no meeting yet)
 *  opens nothing and answers an empty map. */
export function useMeetingSignals(platform: string | undefined, nativeId: string | undefined, enabled = true): AnnotationsBySegment {
  const p = apiPlatform(platform), n = String(nativeId ?? "");
  const on = enabled && !!p && !!n;
  const key = on ? `${p}|${n}` : "";
  const s = storeFor(key);
  useEffect(() => {
    if (!on) return;
    s.refs += 1;
    if (!s.off) {
      s.off = subscribeMeetingStreams(p, n, ["signals"], (data) => {
        const f = parseSignalsFrame(data);
        if (!f || !f.annotations.length) return;
        s.value = mergeAnnotations(s.value, f.annotations);
        s.subs.forEach((cb) => cb());
      });
    }
    return () => {
      s.refs -= 1;
      if (s.refs <= 0 && s.off) { s.off(); s.off = undefined; }
    };
  }, [on, p, n, s]);
  return useSyncExternalStore(
    (cb) => { s.subs.add(cb); return () => { s.subs.delete(cb); }; },
    () => (on ? s.value : EMPTY),
    () => EMPTY,
  );
}
