/**
 * transcript-merge.ts — the segment ADAPTERS + the canonical two-map re-export.
 *
 * The two-map live-transcript model (confirmed append-only + pending-by-speaker, dedup, grouping) is
 * NOT reimplemented here — it is the published `@vexaai/transcript-rendering` package (v0.4.x), the
 * single source of truth the vendored dashboard also consumes. This module re-exports that pipeline
 * and adds the two SHAPE ADAPTERS the dashboard needs, because the bot/REST segment shapes don't match
 * the renderer's required `TranscriptSegment` (text + `absolute_start_time` both required):
 *
 *   • `restSegmentToLive` — a REST `TranscriptionResponse` segment (start/end SECONDS, `absolute_start_time`
 *     from the backend) → the renderer shape.
 *   • `normalizeLiveSeg`  — a LIVE WS `transcript` segment, which carries the time as an EPOCH `start`
 *     (seconds since 1970) with `absolute_start_time` null → DERIVE `absolute_start_time` from the epoch
 *     `start` (and map `start`/`end` → `start_time`/`end_time`). Without this every live segment would be
 *     filtered at ingest and only a REST reload would show transcripts.
 *
 * Adapters keep the impedance on the CLIENT (ADR-0023) — the core publishes its contract verbatim, the
 * dashboard adapts it to the renderer's shape.
 */
import type { TranscriptSegment as RenderSegment } from "@vexaai/transcript-rendering";

export {
  createTranscriptState,
  bootstrapConfirmed,
  applyTranscriptTick,
  recomputeTranscripts,
} from "@vexaai/transcript-rendering";
export type { TranscriptState } from "@vexaai/transcript-rendering";

/** The renderer's segment shape (`@vexaai/transcript-rendering` `TranscriptSegment`). */
export type LiveSegment = RenderSegment;

/** A loose source segment — either the REST or the WS shape, both carrying extra/optional keys. */
type SourceSegment = {
  text?: string | null;
  speaker?: string | null;
  language?: string | null;
  completed?: boolean | null;
  segment_id?: string | null;
  absolute_start_time?: string | null;
  absolute_end_time?: string | null;
  start_time?: number | string | null;
  end_time?: number | string | null;
  /** the LIVE WS / collector shape carries epoch seconds here */
  start?: number | string | null;
  end?: number | string | null;
  updated_at?: string | null;
};

const num = (v: unknown): number =>
  typeof v === "number" ? v : v == null ? NaN : parseFloat(String(v));

/** An epoch (seconds since 1970) → ISO string; anything that isn't a plausible epoch → "". */
const epochToIso = (n: number): string =>
  Number.isFinite(n) && n > 1_000_000_000 ? new Date(n * 1000).toISOString() : "";

/**
 * Normalize a REST `TranscriptionResponse` segment (start/end SECONDS) into the renderer shape. REST
 * confirmed segments carry `absolute_start_time` from the backend (a hard requirement — see the 0.12
 * commit "segments carry absolute_start_time — the dashboard renderer requires it"); pass it through.
 */
export function restSegmentToLive(seg: SourceSegment): RenderSegment {
  return {
    text: seg.text ?? "",
    speaker: seg.speaker ?? undefined,
    absolute_start_time: seg.absolute_start_time ?? "",
    absolute_end_time: seg.absolute_end_time ?? "",
    language: seg.language ?? "en",
    completed: seg.completed ?? true,
    segment_id: seg.segment_id ?? undefined,
    start_time: num(seg.start_time ?? seg.start),
    end_time: num(seg.end_time ?? seg.end),
  } as RenderSegment;
}

/**
 * Normalize a LIVE (WS) segment into the renderer shape. The bot/collector publish each segment's time
 * as an EPOCH `start`/`end` (e.g. `1782162716.032`) with `absolute_start_time` null; the renderer keys
 * + sorts on `absolute_start_time` (and requires it), so DERIVE it from the epoch `start`. Already-ISO
 * segments (REST-shaped) pass through untouched.
 */
export function normalizeLiveSeg(seg: SourceSegment): RenderSegment {
  const startNum = Number.isFinite(num(seg.start_time)) ? num(seg.start_time) : num(seg.start);
  const endNum = Number.isFinite(num(seg.end_time)) ? num(seg.end_time) : num(seg.end);
  return {
    text: seg.text ?? "",
    speaker: seg.speaker ?? undefined,
    absolute_start_time: seg.absolute_start_time ?? epochToIso(startNum),
    absolute_end_time: seg.absolute_end_time ?? epochToIso(endNum),
    language: seg.language ?? undefined,
    completed: seg.completed ?? undefined,
    segment_id: seg.segment_id ?? undefined,
    start_time: Number.isFinite(startNum) ? startNum : undefined,
    end_time: Number.isFinite(endNum) ? endNum : undefined,
    updated_at: seg.updated_at ?? undefined,
  } as RenderSegment;
}
