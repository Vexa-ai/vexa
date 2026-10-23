/** A CARD'S FACE — the client half of the blocks.v1 face profile (#1783, desktop ADR §2.2, D2).
 *
 *  crm-api is the authority: it sanitizes every stored face and resolves it per viewer (a data block
 *  arrives filled, a field this viewer cannot read arrives as `locked`). The browser drops again, so a
 *  face can never draw more than this vocabulary allows, whatever an agent proposed:
 *   - structural blocks (`stepper people movement waiting next subcards ref status`) keep only their
 *     type and draw from the card itself;
 *   - data blocks keep only their declared fields, clipped (labels 40, text 160, rows 4, checklist 10);
 *   - unknown blocks, links, markup and actions are dropped; plain text only. */

export const FACE_LIMITS = { blocks: 8, label: 40, text: 160, rows: 4, checklist: 10 } as const;

export const STRUCTURAL = ["stepper", "people", "movement", "waiting", "next", "subcards", "ref", "status"] as const;
export type StructuralBlock = { type: (typeof STRUCTURAL)[number] };
export type FaceBlock =
  | StructuralBlock
  | { type: "metric"; value: string; label?: string }
  | { type: "text"; text: string; label?: string }
  | { type: "progress"; value: number; label?: string }
  | { type: "due"; at: string; label?: string }
  | { type: "checklist"; items: { text: string; done: boolean }[]; label?: string }
  | { type: "list"; rows: string[]; label?: string }
  | { type: "locked"; label: string };

/** The type's default face, for a card that stores none (crm-api answers it the same way). */
export const DEFAULT_FACE: FaceBlock[] = [
  { type: "stepper" }, { type: "next" }, { type: "waiting" }, { type: "movement" }, { type: "subcards" }, { type: "people" },
];

const clip = (v: unknown, n: number): string => String(v ?? "").slice(0, n);
const str = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function label(b: Record<string, unknown>): { label?: string } {
  return str(b.label) ? { label: clip(b.label, FACE_LIMITS.label) } : {};
}

export function sanitizeFace(face: unknown): FaceBlock[] {
  if (!Array.isArray(face)) return [];
  const out: FaceBlock[] = [];
  for (const raw of face) {
    if (out.length >= FACE_LIMITS.blocks) break;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const b = raw as Record<string, unknown>;
    const type = b.type;
    if ((STRUCTURAL as readonly unknown[]).includes(type)) { out.push({ type } as StructuralBlock); continue; }
    switch (type) {
      case "metric":
        if (str(b.value) || typeof b.value === "number") out.push({ type, value: clip(b.value, FACE_LIMITS.text), ...label(b) });
        break;
      case "text":
        if (str(b.text)) out.push({ type, text: clip(b.text, FACE_LIMITS.text), ...label(b) });
        break;
      case "progress": {
        const v = typeof b.value === "number" ? b.value : NaN;
        if (Number.isFinite(v)) out.push({ type, value: Math.min(1, Math.max(0, v)), ...label(b) });
        break;
      }
      case "due":
        if (str(b.at) && DAY.test(b.at)) out.push({ type, at: b.at, ...label(b) });
        break;
      case "checklist":
        if (Array.isArray(b.items)) out.push({ type, ...label(b), items: b.items.slice(0, FACE_LIMITS.checklist)
          .filter((x) => x && typeof x === "object")
          .map((x) => ({ text: clip((x as { text?: unknown }).text, FACE_LIMITS.text), done: (x as { done?: unknown }).done === true })) });
        break;
      case "list":
        if (Array.isArray(b.rows)) out.push({ type, ...label(b), rows: b.rows.slice(0, FACE_LIMITS.rows).map((r) => clip(r, FACE_LIMITS.text)) });
        break;
      case "locked":
        if (str(b.label)) out.push({ type, label: clip(b.label, FACE_LIMITS.label) });
        break;
      default:
        break; // unknown blocks are dropped
    }
  }
  return out;
}

/** "YYYY-MM-DD" → a short local day ("Wed 22 Oct"); anything else as given. */
export function dayWords(at: string): string {
  if (!DAY.test(at)) return at;
  const [y, m, d] = at.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

/** "2026-10-15T09:00:00Z" or Unix seconds → "3h ago" / "2d ago"; never throws. */
export function ago(at: string | number | null | undefined, now: number = Date.now()): string {
  if (at === null || at === undefined || at === "") return "";
  const t = typeof at === "number" ? (at < 1e12 ? at * 1000 : at) : Date.parse(at);
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
