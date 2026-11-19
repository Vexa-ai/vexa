/** A workspace's STANDARD ICON drawn, and the searchable grid that changes it (founder 2026-10-18:
 *  "let's pick icons from a large selection of standard icons at creation unless changed").
 *
 *  `GlyphSvg` draws one outline icon from its strokes (the generated list, `platform/workspaceGlyphs`)
 *  in `currentColor`, so the tile that holds it decides the colour per theme (`identity.css`). No solid
 *  fills (7r): an outline on a tinted tile, as the monogram tile already is.
 *
 *  `GlyphPicker` is the grid: a search box over every icon's name and words, the suggested one first,
 *  the current one marked. It only reports the pick; the caller stores it. */
import { createElement, useEffect, useMemo, useState } from "react";
import { Check } from "lucide-react";
import { loadGlyphs, searchGlyphs, type GlyphDef } from "../platform/workspaceGlyphs";
import { Input } from "./primitives/Field";

export function GlyphSvg({ def, className }: { def: GlyphDef; className?: string }) {
  return <svg className={className ?? "vx-id-glyph"} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" data-glyph={def.name}>
    {def.nodes.map(([tag, attrs], i) => createElement(tag, { key: i, ...attrs }))}
  </svg>;
}

/** The list, once loaded (`null` while it loads). */
export function useGlyphs(): GlyphDef[] | null {
  const [glyphs, setGlyphs] = useState<GlyphDef[] | null>(null);
  useEffect(() => {
    let live = true;
    void loadGlyphs().then((d) => { if (live) setGlyphs(d.glyphs); });
    return () => { live = false; };
  }, []);
  return glyphs;
}

const label = (name: string) => name.replace(/-\d+$/, "").replace(/-/g, " ");

export function GlyphPicker({ value, suggested, onPick }: { value?: string | null; suggested?: string | null; onPick: (name: string) => void }) {
  const glyphs = useGlyphs();
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    if (!glyphs) return [];
    const hits = searchGlyphs(glyphs, q);
    if (q.trim() || !suggested) return hits;
    const first = hits.find((g) => g.name === suggested);
    return first ? [first, ...hits.filter((g) => g !== first)] : hits;
  }, [glyphs, q, suggested]);
  return <div className="vx-glyphs">
    <Input label="Search icons" hideLabel placeholder={glyphs ? `Search ${glyphs.length} icons` : "Search icons"} value={q}
      onChange={(e) => setQ(e.target.value)} data-autofocus="" type="search" />
    <div className="vx-glyphs-grid" role="group" aria-label="Icons">
      {!glyphs && <span className="vx-glyphs-empty">Loading icons…</span>}
      {glyphs && !shown.length && <span className="vx-glyphs-empty">No icon matches “{q}”</span>}
      {shown.map((g) => <button key={g.name} type="button" className="vx-glyphs-cell" aria-pressed={g.name === value}
        aria-label={label(g.name)} title={label(g.name)} data-suggested={g.name === suggested && !q.trim() ? "" : undefined} onClick={() => onPick(g.name)}>
        <GlyphSvg def={g} />
        {g.name === value && <span className="vx-glyphs-on" aria-hidden><Check size={10} strokeWidth={2.5} /></span>}
      </button>)}
    </div>
  </div>;
}
