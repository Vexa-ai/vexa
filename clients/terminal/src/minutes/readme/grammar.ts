/** THE ONE PAGE GRAMMAR, client half (PRD 0.13.4 D17/D18; UI 7aa, 7gg). The server's
 *  `core/agent/control_plane/page_grammar.py` is the canonical parser; this mirror exists for the
 *  BFF (which must know a page's `{{crm …}}` and card blocks before it asks crm with the viewer's key)
 *  and for the composer (references, paste). Both run `page_grammar.cases.json`, so they cannot drift.
 *
 *    [[kind:id]]  [[kind:id|label]]   an inline reference (anchors: `153@00:12:30`, `153#L42`, `#heading`)
 *    {{name key=value "positional"}}  a live block or an action — never shown raw
 *
 *  ONE VERB REGISTRY: the old `ACTION_VERBS` (`[[action:verb:target|label]]`) and the mock's
 *  `DO_VERBS` (`{{verb …}}`) are one table here; the old spellings are aliases. */

export const REF_KINDS = ["workspace", "room", "chat", "page", "file", "entity", "meeting", "card", "person",
  "routine", "sensor", "message", "query", "series", "join"] as const;
export type RefKind = (typeof REF_KINDS)[number];
export const KIND_ALIASES: Record<string, RefKind> = { call: "meeting", flow: "routine" };
const ALL_KINDS = [...REF_KINDS, ...Object.keys(KIND_ALIASES)];

export const BLOCKS = {
  "waiting-on-you": { title: "Waiting on you", personal: true },
  "waiting-on-others": { title: "Waiting on others", personal: true },
  commitments: { title: "Commitments", personal: false },
  rooms: { title: "Rooms", personal: false },
  people: { title: "People", personal: false },
  routines: { title: "Agents and routines", personal: false },
  meetings: { title: "Meetings", personal: false },
  files: { title: "Files", personal: false },
  pinned: { title: "Pinned", personal: false },
  "changes-since": { title: "What changed", personal: true },
  crm: { title: "", personal: false },
  card: { title: "", personal: false },
  cards: { title: "", personal: false },
} as const;
export type BlockName = keyof typeof BLOCKS;
/** The blocks only the BFF can feed (crm-api, with the viewer's own key). */
export const CRM_BLOCKS: readonly BlockName[] = ["crm"];
export const CARD_BLOCKS: readonly BlockName[] = ["commitments", "waiting-on-others", "card", "cards"];

export type Verb = { name: string; label: string; needs: "reader" | "contributor" | "owner"; confirm: boolean; aliases: string[] };
/** THE ONE VERB REGISTRY (D17). Mirrors `page_grammar.VERBS`. */
export const VERBS: Record<string, Verb> = Object.fromEntries(([
  ["new-chat", "New chat", "reader", false, []],
  ["new-room", "New room", "contributor", true, []],
  ["schedule", "Schedule", "contributor", true, ["schedule-call"]],
  ["invite", "Invite", "contributor", false, []],
  ["ask", "Ask", "reader", false, []],
  ["run", "Run", "contributor", true, ["run-routine"]],
  ["start-call", "Start a call", "contributor", true, []],
  ["join-call", "Join", "reader", false, []],
  ["prepare", "Prepare", "reader", false, []],
  ["accept-proposal", "Accept", "contributor", true, []],
  ["move-card", "Move", "contributor", true, []],
] as const).map(([name, label, needs, confirm, aliases]) => [name, { name, label, needs, confirm, aliases: [...aliases] }]));
const VERB_ALIASES: Record<string, string> = Object.fromEntries(Object.values(VERBS).flatMap((v) => v.aliases.map((a) => [a, v.name])));
/** Every spelling a page may use (canonical names and the old aliases). */
export const ACCEPTED_VERBS: readonly string[] = [...Object.keys(VERBS), ...Object.keys(VERB_ALIASES)];

export function canonicalVerb(name: string): string | null {
  const n = String(name ?? "").trim().toLowerCase();
  const c = VERB_ALIASES[n] ?? n;
  return VERBS[c] ? c : null;
}
export function canonicalKind(kind: string): RefKind | null {
  const k = String(kind ?? "").trim().toLowerCase();
  const c = (KIND_ALIASES[k] ?? k) as RefKind;
  return (REF_KINDS as readonly string[]).includes(c) ? c : null;
}

export type Ref = { kind: RefKind; id: string; anchor?: string; label?: string; key: string };
export type Block = { name: BlockName; args: Record<string, string>; positional: string[]; key: string };

const ID = String.raw`[^\]\[|\s]{1,300}`;
export const REF_SRC = String.raw`\[\[(${ALL_KINDS.join("|")}):(${ID})(?:\|([^\]]{0,200}))?\]\]`;
const LEGACY_ACTION_SRC = String.raw`\[\[action:([a-z-]{1,40}):([^\]|]{0,300})\|([^\]]{1,120})\]\]`;
const BLOCK_SRC = String.raw`\{\{\s*([a-z][a-z-]{0,39})((?:[^{}\n]){0,400})\}\}`;
const ARG_RE = /([a-z_][\w-]{0,39})\s*=\s*("([^"]*)"|'([^']*)'|[^\s"']+)|"([^"]*)"|'([^']*)'|(\S+)/g;
const TIME = /^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$/;
const FENCE = /^```[^\n]*\n[\s\S]*?^```[ \t]*$/gm;

export function normalizeTime(s: string): string | null {
  const m = TIME.exec(String(s ?? "").trim());
  if (!m) return null;
  const h = Number(m[1] ?? 0), mi = Number(m[2]), se = Number(m[3]);
  if (se >= 60 || (m[1] !== undefined && mi >= 60)) return null;
  const t = h * 3600 + mi * 60 + se;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(Math.floor(t / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
}

export function splitAnchor(kind: RefKind, raw: string): { id: string; anchor?: string } {
  let id = String(raw ?? "").trim();
  let anchor: string | undefined;
  const hash = id.indexOf("#");
  if (hash >= 0) { anchor = id.slice(hash + 1).trim() || undefined; id = id.slice(0, hash); }
  if (kind === "meeting" && id.includes("@")) {
    const at = id.slice(id.indexOf("@") + 1);
    id = id.slice(0, id.indexOf("@"));
    const t = normalizeTime(at);
    if (t) anchor = `t=${t}`;
  }
  if (anchor?.startsWith("t=")) { const t = normalizeTime(anchor.slice(2)); anchor = t ? `t=${t}` : undefined; }
  return { id: id.trim(), ...(anchor ? { anchor } : {}) };
}

export function parseRefParts(kind: string, raw: string, label?: string): Ref | null {
  const k = canonicalKind(kind);
  if (!k) return null;
  const { id, anchor } = splitAnchor(k, raw);
  if (!id || id.split("/").some((s) => s === "" || s === "." || s === "..")) return null;
  const lab = (label ?? "").trim();
  return { kind: k, id, ...(anchor ? { anchor } : {}), ...(lab ? { label: lab } : {}), key: `${k}:${id}${anchor ? `#${anchor}` : ""}` };
}

export function parseArgs(s: string): { args: Record<string, string>; positional: string[] } {
  const args: Record<string, string> = {};
  const positional: string[] = [];
  for (const m of String(s ?? "").matchAll(ARG_RE)) {
    if (m[1]) args[m[1].toLowerCase().replace(/_/g, "-")] = (m[3] ?? m[4] ?? m[2]).trim();
    else { const v = (m[5] ?? m[6] ?? m[7] ?? "").trim(); if (v) positional.push(v); }
  }
  return { args, positional };
}

/** The block's stable key — byte-equal to `page_grammar.Block.key`, so the BFF's answers land. */
export function blockKey(name: string, args: Record<string, string>, positional: string[]): string {
  const parts = [name, ...Object.keys(args).sort().map((k) => `${k}="${args[k]}"`), ...positional.map((p) => `"${p}"`)];
  return parts.join(" ");
}

export function refsIn(md: string): Ref[] {
  const out: Ref[] = [];
  const seen = new Set<string>();
  for (const m of String(md ?? "").replace(FENCE, "").matchAll(new RegExp(REF_SRC, "g"))) {
    const r = parseRefParts(m[1], m[2], m[3]);
    if (r && !seen.has(r.key)) { seen.add(r.key); out.push(r); }
  }
  return out;
}

export function blocksIn(md: string): Block[] {
  const out: Block[] = [];
  const seen = new Set<string>();
  for (const m of String(md ?? "").replace(FENCE, "").matchAll(new RegExp(BLOCK_SRC, "g"))) {
    const name = m[1].toLowerCase();
    if (!(name in BLOCKS)) continue;
    const { args, positional } = parseArgs(m[2] ?? "");
    const key = blockKey(name, args, positional);
    if (!seen.has(key)) { seen.add(key); out.push({ name: name as BlockName, args, positional, key }); }
  }
  return out;
}

/** The verbs a page places, in order (for tests and the editor's hints). */
export function verbsIn(md: string): string[] {
  const out: string[] = [];
  const src = String(md ?? "").replace(FENCE, "");
  const re = new RegExp(`${LEGACY_ACTION_SRC}|${BLOCK_SRC}`, "g");
  for (const m of src.matchAll(re)) {
    const v = canonicalVerb(m[1] ?? m[4] ?? "");
    if (v && !(m[4] && m[4].toLowerCase() in BLOCKS)) out.push(v);
  }
  return out;
}

export function refUrl(r: Pick<Ref, "kind" | "id" | "anchor">, base = ""): string {
  const id = r.id.split("/").map((s) => encodeURIComponent(s).replace(/%40/g, "@").replace(/%3A/g, ":")).join("/");
  return `${base.replace(/\/+$/, "")}/r/${r.kind}/${id}${r.anchor ? `#${r.anchor}` : ""}`;
}
export function refChip(r: Pick<Ref, "kind" | "id" | "anchor">, label?: string): string {
  let id = r.id;
  if (r.anchor) id += r.kind === "meeting" && r.anchor.startsWith("t=") ? `@${r.anchor.slice(2)}` : `#${r.anchor}`;
  return label ? `[[${r.kind}:${id}|${label}]]` : `[[${r.kind}:${id}]]`;
}
