"use client";
/** The ONE live-transcript render engine (P23: the terminal RENDERS, it does not re-derive). It paints a
 *  list of segments — CONFIRMED text is stable + append-only (consecutive same-speaker segments merge into
 *  one flowing block) and the in-flight PENDING text is a single dimmed "live" tail — so the body never
 *  flickers while the unconfirmed window re-forms. Fed RAW segments (transcript) or PROCESSED segments
 *  (the cleaned mirror, which also carry keyword `tags` to research) by exactly the same code; the toggle
 *  picks the source, not the engine. */

export interface EngineTag { label: string; kind: string }
export interface EngineSegment { speaker?: string; text: string; tsMs?: number; id?: string; completed?: boolean; tags?: EngineTag[] }

const TAG_HUE: Record<string, string> = { person: "#2563eb", company: "#7c3aed", product: "#0d9488", number: "#b45309" };

export function LiveTranscriptEngine({ segments, emptyLabel = "Waiting for transcript…" }: { segments: EngineSegment[]; emptyLabel?: string }) {
  // Confirmed (completed !== false) = stable. Merge consecutive same-speaker confirmed segments into
  // flowing blocks; keyword tags accumulate per block. Pending (completed === false) = the live edge.
  const blocks: { speaker?: string; tsMs?: number; text: string; key: string; tags: EngineTag[] }[] = [];
  for (const s of segments) {
    if (s.completed === false) continue;
    const last = blocks[blocks.length - 1];
    if (last && last.speaker === s.speaker) { last.text += " " + s.text; if (s.tags) last.tags.push(...s.tags); }
    else blocks.push({ speaker: s.speaker, tsMs: s.tsMs, text: s.text, key: s.id ?? `b${blocks.length}`, tags: [...(s.tags ?? [])] });
  }
  const lastPending = [...segments].reverse().find((s) => s.completed === false);
  const live = (lastPending?.text ?? "").trim();
  const liveSpeaker = lastPending?.speaker;

  const lastBlock = blocks[blocks.length - 1];
  const liveJoinsLast = !!live && !!lastBlock && lastBlock.speaker === liveSpeaker;
  const liveOwnBlock = !!live && !liveJoinsLast;

  if (!blocks.length && !live) {
    return <div style={{ color: "var(--t3)", fontSize: 13, padding: "8px 2px" }}>{emptyLabel}</div>;
  }

  const head = (speaker?: string, tsMs?: number) =>
    speaker ? (
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--t2)", marginBottom: 3 }}>
        {speaker}
        {typeof tsMs === "number" && (
          <span style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>{new Date(tsMs).toLocaleTimeString()}</span>
        )}
      </div>
    ) : null;

  // dedupe tags by lowercased label (a keyword mentioned twice in a block shows once)
  const chips = (tags: EngineTag[]) => {
    const seen = new Set<string>();
    const uniq = tags.filter((t) => t.label && !seen.has(t.label.toLowerCase()) && seen.add(t.label.toLowerCase()));
    if (!uniq.length) return null;
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        {uniq.map((t, i) => {
          const hue = TAG_HUE[t.kind] ?? "var(--t2)";
          return (
            <span key={`${t.label}-${i}`} title={`research ${t.label}`}
              style={{ fontSize: 11, color: hue, border: `1px solid ${hue}`, background: "transparent", borderRadius: 999, padding: "1px 8px", lineHeight: 1.5, opacity: 0.9 }}>
              {t.label}
            </span>
          );
        })}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13, maxWidth: 760 }}>
      {blocks.map((b, idx) => (
        <div key={b.key}>
          {head(b.speaker, b.tsMs)}
          <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.6 }}>
            {b.text}
            {idx === blocks.length - 1 && liveJoinsLast && (
              <span style={{ color: "var(--t3)", fontStyle: "italic" }}> {live} …</span>
            )}
          </div>
          {chips(b.tags)}
        </div>
      ))}
      {liveOwnBlock && (
        <div>
          {head(liveSpeaker)}
          <div style={{ fontSize: 13.5, color: "var(--t3)", lineHeight: 1.6, fontStyle: "italic" }}>{live} …</div>
        </div>
      )}
    </div>
  );
}
