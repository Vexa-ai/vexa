/** identity — the deterministic half of the one identity system (UI decisions 15f, 15g).
 *
 *  Everything that has a face — a person, a company, a workspace, an agent — shows its stored image
 *  when the server has one, and otherwise a monogram on a colour derived from a STABLE id: the same
 *  answer on every load, on every device, for every viewer. Pure functions, no DOM.
 *
 *  The colour is one of a curated set of hues; `ui-kit/identity.css` turns a hue into a background
 *  and a text colour per theme, so every monogram reads in both dark and light. */

export type IdentityKind = "person" | "company" | "workspace" | "agent";

/** Twelve hues spaced for distinctness, avoiding the muddy yellow-greens. */
export const IDENTITY_HUES = [4, 24, 40, 88, 146, 172, 192, 212, 232, 258, 284, 326] as const;

/** FNV-1a, 32-bit, unsigned — stable across engines and loads. */
export function hashId(id: string): number {
  let h = 0x811c9dc5;
  const s = String(id ?? "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function identityColour(id: string): { index: number; hue: number } {
  const index = hashId(id) % IDENTITY_HUES.length;
  return { index, hue: IDENTITY_HUES[index] };
}

const WORD = /[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu;

/** Up to two letters: the first letters of the first two words, or the first letter of one word.
 *  An email address is read as its local part. Empty → "?". */
export function monogram(name: string): string {
  let s = String(name ?? "").trim();
  if (s.includes("@") && !/\s/.test(s)) s = s.split("@")[0];
  const words = s.match(WORD) ?? [];
  if (!words.length) return "?";
  const first = (w: string) => Array.from(w)[0].toLocaleUpperCase();
  const [a = "", b] = words;
  return b === undefined ? first(a) : first(a) + first(b);
}

/** An agent's or routine's generated mark: one of six simple shapes on its colour. */
export const AGENT_SHAPES = 6;
export function agentMark(id: string): { shape: number; hue: number } {
  const h = hashId(`agent:${id}`);
  return { shape: (h >>> 8) % AGENT_SHAPES, hue: identityColour(id).hue };
}

export type IdentitySource = {
  kind: IdentityKind;
  /** a URL the server gives (a person's photo route, an agent's stored mark) */
  src?: string;
  /** a workspace's slug: its image route */
  slug?: string;
  /** a media-proxy key (`g:…` avatar) for a person who uses Vexa */
  avatar?: string;
  /** a media-proxy key (`d:<domain>`) for a company's logo */
  logo?: string;
  /** a person who uses Vexa; a CRM person who is not one never shows a face */
  user?: boolean;
};

/** Fired (detail = slug) after a workspace's image changed: every drawn identity re-asks it. */
export const WORKSPACE_IMAGE_CHANGED = "vexa:workspace-image-changed";

export const media = (key: string) => `/api/home/media/${encodeURIComponent(key)}`;
/** A Vexa user's photo, served by the media route that checks the viewer shares something with them. */
export const personPhotoUrl = (subject: string) => `/api/people/${encodeURIComponent(subject)}/photo`;
export const workspaceIconUrl = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/identity/icon`;

/** The image to try first, or undefined for "draw the monogram". */
export function identitySrc(s: IdentitySource): string | undefined {
  switch (s.kind) {
    case "person":
      if (!s.user) return undefined;
      return s.src || (s.avatar ? media(s.avatar) : undefined);
    case "company":
      return s.src || (s.logo ? media(s.logo) : undefined);
    case "workspace":
      return s.src || (s.slug ? workspaceIconUrl(s.slug) : undefined);
    case "agent":
      return s.src || undefined;
  }
}

/** The shades `ui-kit/identity.css` draws a hue with, per theme (saturation %, lightness %) —
 *  stated here so the contrast of every hue is tested (`identity.test.ts`); keep the two in step. */
export const IDENTITY_SHADES = {
  dark: { bg: [38, 34], fg: [40, 93] },
  light: { bg: [52, 87], fg: [48, 24] },
} as const;
