/** workspaceGlyphs — a workspace's STANDARD ICON (founder 2026-10-18: "let's pick icons from a large
 *  selection of standard icons at creation unless changed").
 *
 *  Every workspace shows an icon of the outline set the product draws with (lucide). At creation one is
 *  picked from the workspace's name and purpose by a cheap deterministic matcher — the same words, the
 *  same normalisation and the same tie-break as the server's `control_plane/workspace_glyph.py`, from
 *  the same generated list (`workspaceGlyphs.data.ts`, `scripts/gen-workspace-glyphs.mjs`). The person
 *  changes it from a searchable grid; a letter tile is only the fallback when no icon is known.
 *
 *  Pure functions, no DOM; the data module is imported lazily (`loadGlyphs`) so the main bundle does
 *  not carry ~240 icons it draws only on demand. */
import type { GlyphDef } from "./workspaceGlyphs.data";

export type { GlyphDef, GlyphNode } from "./workspaceGlyphs.data";

const WORD = /[\p{L}\p{N}]+/gu;

/** One normalisation for keywords and words alike: lower case, accents off, a plural or "-ing" off. */
export function stem(word: string): string {
  let w = word.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  if (w.length > 4 && w.endsWith("ies")) w = `${w.slice(0, -3)}y`;
  else if (w.length > 5 && w.endsWith("ing")) w = w.slice(0, -3);
  else if (w.length > 4 && /(ss|x|ch|sh)es$/.test(w)) w = w.slice(0, -2);
  else if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) w = w.slice(0, -1);
  return w;
}

/** The words of a text, stemmed, plus every adjacent pair ("central bank"). */
export function terms(text: string): string[] {
  const words = (String(text ?? "").match(WORD) ?? []).map(stem);
  const out = [...words];
  for (let i = 0; i + 1 < words.length; i++) out.push(`${words[i]} ${words[i + 1]}`);
  return out;
}

const keyOf = (k: string) => k.replace(/^~/, "").split(/\s+/).map(stem).join(" ");
const weightOf = (k: string) => (k.startsWith("~") ? 1 : 3);

type Index = Map<string, { name: string; w: number }[]>;
const INDEX = new WeakMap<GlyphDef[], Index>();
function index(glyphs: GlyphDef[]): Index {
  let ix = INDEX.get(glyphs);
  if (ix) return ix;
  ix = new Map();
  for (const g of glyphs) {
    for (const k of g.keywords) {
      const key = keyOf(k);
      const list = ix.get(key) ?? [];
      if (!list.some((e) => e.name === g.name)) list.push({ name: g.name, w: weightOf(k) });
      ix.set(key, list);
    }
  }
  INDEX.set(glyphs, ix);
  return ix;
}

/** FNV-1a, 32-bit — the same as `identity.hashId` and the server's. */
function fnv(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}

/** The icon a workspace called `name`, for `purpose`, gets: the best keyword match (name words count
 *  twice what purpose words do; a weak keyword a third of a specific one; ties go to the list's order),
 *  or `null` when nothing matched — the caller then asks a small model, or takes `genericGlyph`. */
export function matchGlyph(glyphs: GlyphDef[], name: string, purpose = ""): string | null {
  const ix = index(glyphs);
  const score = new Map<string, number>();
  const add = (text: string, mult: number) => {
    for (const t of new Set(terms(text))) for (const e of ix.get(t) ?? []) score.set(e.name, (score.get(e.name) ?? 0) + e.w * mult);
  };
  add(name, 2);
  add(purpose, 1);
  let best: string | null = null;
  let top = 0;
  for (const g of glyphs) {
    const s = score.get(g.name) ?? 0;
    if (s > top) { top = s; best = g.name; }
  }
  return best;
}

/** A neutral icon for a name nothing matched — stable for the same id. */
export function genericGlyph(generic: string[], id: string): string {
  return generic[fnv(String(id ?? "")) % generic.length];
}

/** Picker search: every word of the query must start a word of the icon's name or a keyword. */
export function searchGlyphs(glyphs: GlyphDef[], query: string): GlyphDef[] {
  const q = (String(query ?? "").toLowerCase().match(WORD) ?? []);
  if (!q.length) return glyphs;
  return glyphs.filter((g) => {
    const words = [...g.name.split("-"), ...g.keywords.flatMap((k) => k.replace(/^~/, "").split(/\s+/))];
    return q.every((w) => words.some((x) => x.startsWith(w) || stem(x) === stem(w)));
  });
}

/** The glyph list, loaded once on demand. */
let loading: Promise<{ glyphs: GlyphDef[]; generic: string[] }> | null = null;
let loaded: { glyphs: GlyphDef[]; generic: string[]; byName: Map<string, GlyphDef> } | null = null;
export function loadGlyphs(): Promise<{ glyphs: GlyphDef[]; generic: string[] }> {
  if (!loading) {
    loading = import("./workspaceGlyphs.data").then((m) => {
      loaded = { glyphs: m.GLYPHS, generic: m.GENERIC_GLYPHS, byName: new Map(m.GLYPHS.map((g) => [g.name, g])) };
      return loaded;
    });
  }
  return loading;
}
/** The glyph by name once the list is loaded (synchronous; `undefined` before or when unknown). */
export function glyphNow(name: string | undefined | null): GlyphDef | undefined {
  return name && loaded ? loaded.byName.get(name) : undefined;
}
export const glyphsLoaded = () => loaded !== null;
