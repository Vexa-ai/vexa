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

export const REF_KINDS = ["workspace", "chat", "page", "entity", "meeting", "flow", "sensor"] as const;
export interface UniversalRef { kind: string; id: string; label?: string }

const KIND_ALT = REF_KINDS.join("|");
const ID = String.raw`[^\s<>"'#?|\]\[]{1,300}`;
const URL_RE = new RegExp(String.raw`(?:https?://[^\s/<>"']+)?/r/(${KIND_ALT})/(${ID})`);
const CHIP_RE = new RegExp(String.raw`^\[\[(${KIND_ALT}):(${ID})(?:\|([^\]]*))?\]\]$`);
const ANY_RE = new RegExp(String.raw`\[\[(?:${KIND_ALT}):${ID}(?:\|[^\]]*)?\]\]|https?://[^\s/<>"']+/r/(?:${KIND_ALT})/${ID}`, "g");
const TRAILING = /[.,;:!)]+$/;

function cleanId(raw: string): string | null {
  let id = raw.replace(TRAILING, "").replace(/^\/+|\/+$/g, "");
  try { id = decodeURIComponent(id); } catch { /* keep as typed */ }
  if (!id || id.split("/").some((s) => s === "" || s === "." || s === "..")) return null;
  return id;
}

export function refUrl(kind: string, id: string, base = ""): string {
  return `${base.replace(/\/+$/, "")}/r/${kind}/${id.split("/").map(encodeURIComponent).join("/")}`;
}

export function refChip(kind: string, id: string, label?: string): string {
  return label ? `[[${kind}:${id}|${label}]]` : `[[${kind}:${id}]]`;
}

/** A chip or a `/r/` URL → the reference; anything else (a `[[Title]]`, prose) → null. */
export function parseUniversalRef(text: string): UniversalRef | null {
  const t = (text ?? "").trim();
  const c = CHIP_RE.exec(t);
  if (c) {
    const id = cleanId(c[2]);
    const label = (c[3] ?? "").trim();
    return id ? { kind: c[1], id, label: label || undefined } : null;
  }
  const u = new RegExp(`^${URL_RE.source}$`).exec(t.split("#")[0].split("?")[0]);
  if (u) {
    const id = cleanId(u[2]);
    return id ? { kind: u[1], id, label: undefined } : null;
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
  return parts.map((p) => (p.kind === "ref" ? refChip(p.ref.kind, p.ref.id, p.ref.label) : p.text)).join("");
}

// ── resolution: the server's answer, briefly cached ──────────────────────────────────────────────
const TTL_MS = 30_000;
const cache = new Map<string, { at: number; p: Promise<RefAnswer> }>();

export function invalidateRefCache(): void { cache.clear(); }

const unknownAnswer = (r: UniversalRef | null): RefAnswer => ({
  kind: r?.kind ?? "", id: r?.id ?? "", url: r ? refUrl(r.kind, r.id) : "", access: "gone",
  label: null, open: null, request_access: false,
});

export function resolveUniversalRef(text: string): Promise<RefAnswer> {
  const hit = cache.get(text);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.p;
  const p = import("../data/workspaceApi")
    .then((api) => api.resolveRef(text))
    .catch(() => unknownAnswer(parseUniversalRef(text)));
  cache.set(text, { at: Date.now(), p });
  return p;
}
