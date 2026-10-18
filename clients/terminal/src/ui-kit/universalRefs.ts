/** universalRefs — the one reference grammar on the client (#1783).
 *
 *  Founder, 2026-10-14: *"references to workspaces, chats, documents, etc., accepted everywhere where
 *  access is confirmed."* Two spellings of one pair (kind, id), mirroring `core/workspaces/shared/links.py`:
 *
 *    https://<host>/r/<kind>/<id>     what a person pastes, from anywhere
 *    [[<kind>:<id>|<label>]]          the chip the agent writes (the label is the writer's hint only)
 *
 *  What a reference points at, and whether this reader may see it, is the SERVER's answer
 *  (`GET /api/refs/resolve`), cached briefly here. The client never derives a path or a slug from
 *  a name: that is how `[[mühlemann+popp AG]]` opened a page that did not exist. */
import type { RefAnswer } from "../data/workspaceApi";

/** Every kind (PRD 0.13.4 D18, UI 7gg) — the same list as `links.REF_KINDS` and `readme/grammar.ts`. */
export const REF_KINDS = ["workspace", "room", "chat", "page", "file", "entity", "meeting", "card", "person",
  "routine", "sensor", "message", "query", "series", "flow"] as const;
/** `anchor`: a place inside the object — `t=00:12:30` (a meeting moment), `L42`, a heading slug. */
export interface UniversalRef { kind: string; id: string; label?: string; anchor?: string }

const KIND_ALT = [...REF_KINDS, "call"].join("|");
const ID = String.raw`[^\s<>"'#?|\]\[]{1,300}`;
const CHIP_ID = String.raw`[^\s<>"'#|\]\[]{1,300}`;
const FRAG = String.raw`(?:#([\w=:.-]{1,120}))?`;
const URL_RE = new RegExp(String.raw`(?:https?://[^\s/<>"']+)?/r/(${KIND_ALT})/(${ID})`);
const CHIP_RE = new RegExp(String.raw`^\[\[(${KIND_ALT}):(${CHIP_ID})${FRAG}(?:\|([^\]]*))?\]\]$`);
const ANY_RE = new RegExp(String.raw`\[\[(?:${KIND_ALT}):${CHIP_ID}${FRAG}(?:\|[^\]]*)?\]\]|https?://[^\s/<>"']+/r/(?:${KIND_ALT})/${ID}(?:#[\w=:.-]{1,120})?`, "g");

function anchorOf(kind: string, id: string, frag?: string): { id: string; anchor?: string } {
  let a = frag || undefined;
  let rid = id;
  const at = /^(.+?)@((?:\d{1,2}:)?\d{1,2}:\d{2})$/.exec(id);
  if (kind === "meeting" && at) { rid = at[1]; a = `t=${at[2]}`; }
  if (a?.startsWith("t=")) {
    const p = a.slice(2).split(":").map(Number);
    if (p.length < 2 || p.some((n) => !Number.isFinite(n)) || p[p.length - 1] >= 60) a = undefined;
    else {
      const [h, m, s] = p.length === 3 ? p : [0, p[0], p[1]];
      const t = h * 3600 + m * 60 + s;
      const z = (n: number) => String(n).padStart(2, "0");
      a = `t=${z(Math.floor(t / 3600))}:${z(Math.floor((t % 3600) / 60))}:${z(t % 60)}`;
    }
  }
  return { id: rid, ...(a ? { anchor: a } : {}) };
}
const TRAILING = /[.,;:!)]+$/;

function cleanId(raw: string): string | null {
  let id = raw.replace(TRAILING, "").replace(/^\/+|\/+$/g, "");
  try { id = decodeURIComponent(id); } catch { /* keep as typed */ }
  if (!id || id.split("/").some((s) => s === "" || s === "." || s === "..")) return null;
  return id;
}

export function refUrl(kind: string, id: string, base = "", anchor?: string): string {
  return `${base.replace(/\/+$/, "")}/r/${kind}/${id.split("/").map(encodeURIComponent).join("/")}${anchor ? `#${anchor}` : ""}`;
}

export function refChip(kind: string, id: string, label?: string, anchor?: string): string {
  const rid = anchor ? id + (kind === "meeting" && anchor.startsWith("t=") ? `@${anchor.slice(2)}` : `#${anchor}`) : id;
  return label ? `[[${kind}:${rid}|${label}]]` : `[[${kind}:${rid}]]`;
}

/** A chip or a `/r/` URL → the reference; anything else (a `[[Title]]`, prose) → null. */
export function parseUniversalRef(text: string): UniversalRef | null {
  const t = (text ?? "").trim();
  const c = CHIP_RE.exec(t);
  if (c) {
    const kind = c[1] === "call" ? "meeting" : c[1];
    const { id: raw, anchor } = anchorOf(kind, c[2], c[3]);
    const id = cleanId(raw);
    const label = (c[4] ?? "").trim();
    return id ? { kind, id, label: label || undefined, ...(anchor ? { anchor } : {}) } : null;
  }
  const frag = t.includes("#") ? t.slice(t.indexOf("#") + 1) : undefined;
  const u = new RegExp(`^${URL_RE.source}$`).exec(t.split("#")[0].split("?")[0]);
  if (u) {
    const kind = u[1] === "call" ? "meeting" : u[1];
    const { id: raw, anchor } = anchorOf(kind, u[2], frag && /^[\w=:.-]{1,120}$/.test(frag) ? frag : undefined);
    const id = cleanId(raw);
    return id ? { kind, id, label: undefined, ...(anchor ? { anchor } : {}) } : null;
  }
  return null;
}

export type RefPart = { kind: "text"; text: string } | { kind: "ref"; text: string; ref: UniversalRef };

/** Text → words and references, in order (chips and bare `/r/` URLs). A URL's trailing punctuation
 *  stays text. */
export function splitRefs(text: string): RefPart[] {
  const out: RefPart[] = [];
  const s = text ?? "";
  let last = 0;
  for (const m of s.matchAll(ANY_RE)) {
    let raw = m[0];
    const trail = raw.startsWith("[[") ? "" : (TRAILING.exec(raw)?.[0] ?? "");
    if (trail) raw = raw.slice(0, -trail.length);
    const ref = parseUniversalRef(raw);
    if (!ref) continue;
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: "text", text: s.slice(last, at) });
    out.push({ kind: "ref", text: raw, ref });
    last = at + raw.length;
  }
  if (last < s.length) out.push({ kind: "text", text: s.slice(last) });
  return out;
}

/** What a paste into the composer becomes: every `/r/` link a chip; null when the paste holds none
 *  (the browser's own paste then runs). */
export function pastedRefs(text: string): string | null {
  const parts = splitRefs(text);
  if (!parts.some((p) => p.kind === "ref" && !p.text.startsWith("[["))) return null;
  return parts.map((p) => (p.kind === "ref" ? refChip(p.ref.kind, p.ref.id, p.ref.label, p.ref.anchor) : p.text)).join("");
}

// ── resolution: the server's answer, briefly cached ──────────────────────────────────────────────
const TTL_MS = 30_000;
const cache = new Map<string, { at: number; p: Promise<RefAnswer> }>();

export function invalidateRefCache(): void { cache.clear(); }

const unknownAnswer = (r: UniversalRef | null): RefAnswer => ({
  kind: r?.kind ?? "", id: r?.id ?? "", url: r ? refUrl(r.kind, r.id) : "", access: "gone",
  label: null, open: null, request_access: false,
});

/** A card lives in crm-api, which agent-api cannot read: its chip asks crm with the reader's own key
 *  (the desktop card BFF). A card the reader may not read answers like any other: no name. */
async function resolveCard(r: UniversalRef): Promise<RefAnswer> {
  const base = { kind: "card", id: r.id, url: refUrl("card", r.id), request_access: false };
  try {
    const res = await fetch("/api/desktop/card/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ card: r.id }) });
    if (!res.ok) return { ...base, access: "unavailable", label: null, open: null };
    const c = (await res.json()) as { card?: { title?: string }; title?: string };
    const title = c?.card?.title ?? c?.title;
    return { ...base, access: "readable", label: title || "Card", open: { card: r.id } };
  } catch { return { ...base, access: "unavailable", label: null, open: null }; }
}

export function resolveUniversalRef(text: string): Promise<RefAnswer> {
  const hit = cache.get(text);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.p;
  const parsed = parseUniversalRef(text);
  const p = (parsed?.kind === "card" ? resolveCard(parsed) : import("../data/workspaceApi")
    .then((api) => api.resolveRef(text)))
    .catch(() => unknownAnswer(parseUniversalRef(text)));
  cache.set(text, { at: Date.now(), p });
  return p;
}
