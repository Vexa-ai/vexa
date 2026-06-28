/**
 * gmeet-capture.v1 — the capture→pipeline contract for the GMEET lane.
 *
 * Google Meet exposes per-participant audio channels AND the glow (active-speaker)
 * indicator, so the speaker name is bound onto each channel AT THE SOURCE. Unlike
 * mixed-capture.v1 (one nameless mixed stream, named downstream from hints), every
 * gmeet frame is already named:
 *
 *   FRAME = { track: int, ts: float(epoch ms), speakerName: string, pcm: Float32[] }
 *
 * `track` is the per-participant channel id (replaces the vague "channel"); Meet
 * rotates a small pool of remote channels across talkers, so `track` is NOT a
 * stable speaker — identity rides on `speakerName` (the glow name lit at `ts`),
 * never on the track index. Wire serialization is @vexa/capture-codec's NAMED
 * binary frame (high-bit `track` + UTF-8 name); chat/lifecycle ride its event JSON.
 *
 * Producer: @vexa/gmeet-capture. Consumer: @vexa/gmeet-pipeline (channel router) →
 * transcript.v1. No downstream namer, no diarization.
 */
import type { MeetingEvent } from '@vexa/capture-codec';

/** One named per-channel audio frame. */
export interface GmeetFrame {
  /** Per-participant channel id (rotates across talkers — not a stable speaker). */
  track: number;
  ts: number;               // CAPTURE epoch ms — stamped at the source, never restamped
  /** Glow name bound at the source at `ts` (empty only during a brief unbound gap). */
  speakerName: string;
  pcm: Float32Array;        // 16 kHz mono PCM
}

/** gmeet also emits chat + lifecycle on the shared @vexa/capture-codec event JSON. */
export type GmeetEvent = MeetingEvent;
