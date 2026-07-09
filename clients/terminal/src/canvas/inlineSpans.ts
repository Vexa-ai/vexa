/** Pure helper: split a note/block of text into a flat list of segments where any entity LABEL that
 *  appears (whole-word, case-insensitive) is wrapped in its own span carrying the entity's kind + the
 *  source entity, so the renderer can paint + make it clickable inline. Plain segments carry no entity.
 *
 *  Re-finding the label by string (rather than trusting pre-merge offsets) keeps inline highlighting
 *  resilient to the engine's speaker-merge (blocks concatenate several segments' text) and to the live
 *  pending tail — we never index into a stale offset, we just match the current block text. */

export interface SpanEntity {
  /** stable id of the matched entity (for React keys + actions) */
  id?: string;
  /** display label that was matched in the text */
  label: string;
  /** entity kind (person / company / product / number / …) — drives the highlight color */
  kind: string;
  /** optional canonical doc path for "Open entity doc" */
  docPath?: string;
}

export interface TextSpan {
  text: string;
  /** present when this span is a matched entity mention */
  entity?: SpanEntity;
}

function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Longest-label-first so "Acme Corp" wins over a bare "Acme" when both are entities. */
function byLabelLengthDesc(a: { label: string }, b: { label: string }): number {
  return b.label.length - a.label.length;
}

/**
 * Split `text` into ordered spans. Each entity whose `label` is found as a whole word is wrapped in a
 * span tagged with that entity; the surrounding text stays in plain spans. Non-overlapping, first match
 * per entity, earliest position wins. Returns a single plain span for empty/entity-free text.
 */
export function splitTextIntoSpans(text: string, entities: SpanEntity[]): TextSpan[] {
  const source = text ?? "";
  if (!source) return [];
  const candidates = (entities ?? []).filter((e) => e && typeof e.label === "string" && e.label.trim().length >= 2);
  if (!candidates.length) return [{ text: source }];

  // Find every whole-word match, then keep non-overlapping ones left-to-right (longest label preferred).
  type Hit = { at: number; end: number; entity: SpanEntity };
  const hits: Hit[] = [];
  for (const entity of [...candidates].sort(byLabelLengthDesc)) {
    const re = new RegExp(`(^|[^\\p{L}\\p{N}_])(${escapeRe(entity.label.trim())})(?=$|[^\\p{L}\\p{N}_])`, "giu");
    let m: RegExpExecArray | null;
    while ((m = re.exec(source))) {
      const at = m.index + m[1].length;
      hits.push({ at, end: at + m[2].length, entity });
      if (re.lastIndex === m.index) re.lastIndex += 1; // guard zero-width
    }
  }
  if (!hits.length) return [{ text: source }];

  hits.sort((a, b) => a.at - b.at || (b.end - b.at) - (a.end - a.at));
  const chosen: Hit[] = [];
  let cursor = 0;
  for (const hit of hits) {
    if (hit.at < cursor) continue; // overlaps an already-chosen span
    chosen.push(hit);
    cursor = hit.end;
  }

  const spans: TextSpan[] = [];
  let pos = 0;
  for (const hit of chosen) {
    if (hit.at > pos) spans.push({ text: source.slice(pos, hit.at) });
    spans.push({ text: source.slice(hit.at, hit.end), entity: hit.entity });
    pos = hit.end;
  }
  if (pos < source.length) spans.push({ text: source.slice(pos) });
  return spans;
}
