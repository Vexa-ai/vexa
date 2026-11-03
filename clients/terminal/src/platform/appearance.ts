/** APPEARANCE — the person's accent colour and fonts (founder, live app.dev 2026-10-15: "we have
 *  this accent color which we might want to change, so let's allow users to pick one; also we want
 *  to be able to choose fonts").
 *
 *  Everything is applied through the design tokens (`app/tokens.css`): one generated stylesheet
 *  re-declares `--accent`, `--accent-hover`, `--on-accent`, `--accent-text`, `--accent-tint`, the
 *  font tokens and the density for both themes, so no component holds a colour of its own. Stored
 *  per person on the server (`/api/user/appearance`, so it follows them to every device), over a
 *  company default in `_global/POLICIES.md`; this browser keeps the last answer only to paint the
 *  first frame without a flash.
 *
 *  Contrast is enforced, not hoped for: a custom colour is moved, in small steps, until text on it
 *  reads at 4.5:1 and it reads as text at 4.5:1 on the page — and the dialog says when it moved. */

import PALETTE from "./appearance-palette.json";

export type Theme = "dark" | "light";
export type Appearance = {
  /** a curated accent id, or a custom `#rrggbb` */
  accent: string;
  font_ui: FontUi; font_reading: FontReading; font_mono: FontMono;
  density: "comfortable" | "compact";
  text_size: "default" | "large";
};

/** Curated accents: a seed per theme, each passing the contrast rules as it stands. */
export const ACCENTS = PALETTE.accents as readonly { id: string; name: string; dark: string; light: string }[];

export const FONTS_UI = {
  geist: { name: "Geist (default)", stack: 'var(--font-geist-sans, -apple-system), BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif' },
  system: { name: "System UI", stack: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, system-ui, sans-serif' },
  inter: { name: "Inter", stack: '"Inter", system-ui, sans-serif', google: "Inter:wght@400;500;600;700" },
  plex: { name: "IBM Plex Sans", stack: '"IBM Plex Sans", system-ui, sans-serif', google: "IBM+Plex+Sans:wght@400;500;600;700" },
} as const;
export const FONTS_READING = {
  ui: { name: "Same as interface", stack: "var(--font-sans)" },
  inter: FONTS_UI.inter,
  plex: FONTS_UI.plex,
  "source-serif": { name: "Source Serif", stack: '"Source Serif 4", Georgia, serif', google: "Source+Serif+4:opsz,wght@8..60,400;8..60,600" },
} as const;
export const FONTS_MONO = {
  geist: { name: "Geist Mono (default)", stack: 'var(--font-geist-mono, ui-monospace), "SF Mono", Menlo, monospace' },
  "plex-mono": { name: "IBM Plex Mono", stack: '"IBM Plex Mono", ui-monospace, Menlo, monospace', google: "IBM+Plex+Mono:wght@400;500" },
  jetbrains: { name: "JetBrains Mono", stack: '"JetBrains Mono", ui-monospace, Menlo, monospace', google: "JetBrains+Mono:wght@400;500" },
} as const;
export type FontUi = keyof typeof FONTS_UI;
export type FontReading = keyof typeof FONTS_READING;
export type FontMono = keyof typeof FONTS_MONO;

export const DEFAULT_APPEARANCE: Appearance = { accent: "clay", font_ui: "geist", font_reading: "ui", font_mono: "geist", density: "comfortable", text_size: "default" };

/** Keep only what this vocabulary knows; anything else falls back to the default. */
export function normalizeAppearance(raw: unknown, base: Appearance = DEFAULT_APPEARANCE): Appearance {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const accent = typeof r.accent === "string" && (ACCENTS.some((a) => a.id === r.accent) || HEX.test(r.accent)) ? r.accent.toLowerCase() : base.accent;
  const pick = <T extends string>(v: unknown, allowed: readonly string[], d: T): T => (typeof v === "string" && allowed.includes(v) ? (v as T) : d);
  return {
    accent,
    font_ui: pick(r.font_ui, Object.keys(FONTS_UI), base.font_ui),
    font_reading: pick(r.font_reading, Object.keys(FONTS_READING), base.font_reading),
    font_mono: pick(r.font_mono, Object.keys(FONTS_MONO), base.font_mono),
    density: pick(r.density, ["comfortable", "compact"], base.density),
    text_size: pick(r.text_size, ["default", "large"], base.text_size),
  };
}

// ── colour ────────────────────────────────────────────────────────────────────────────────────
const HEX = /^#[0-9a-f]{6}$/i;
type RGB = [number, number, number];
const rgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB;
const hex = (c: RGB) => `#${c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("")}`;
const mix = (a: string, b: string, t: number) => { const x = rgb(a), y = rgb(b); return hex([0, 1, 2].map((i) => x[i] + (y[i] - x[i]) * t) as RGB); };
function lum(h: string): number {
  return rgb(h).map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; })
    .reduce((s, c, i) => s + c * [0.2126, 0.7152, 0.0722][i], 0);
}
/** WCAG contrast ratio of two colours */
export function contrast(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

/** the page each theme puts accent text on (tokens.css `--surface-1`) */
export const PAGE: Record<Theme, string> = PALETTE.page;
const DARK_INK = PALETTE.darkInk;
const WHITE = PALETTE.white;
const BLACK = PALETTE.black;

export type AccentTokens = { accent: string; hover: string; on: string; text: string; tint: string; adjusted: boolean };

/** The five accent tokens for one theme from one seed, moved until the contrast rules hold:
 *  text on the accent ≥ 4.5:1, the accent as text on the page ≥ 4.5:1. */
export function deriveAccent(seed: string, theme: Theme): AccentTokens {
  let accent = HEX.test(seed) ? seed.toLowerCase() : ACCENTS[0].dark;
  let adjusted = false;
  let on: string;
  if (theme === "light") {
    on = WHITE;
    for (let i = 0; i < 40 && contrast(accent, on) < 4.5; i++) { accent = mix(accent, BLACK, 0.06); adjusted = true; }
  } else {
    on = contrast(accent, WHITE) >= contrast(accent, DARK_INK) ? WHITE : DARK_INK;
    const toward = on === WHITE ? BLACK : WHITE;
    for (let i = 0; i < 40 && contrast(accent, on) < 4.5; i++) { accent = mix(accent, toward, 0.06); adjusted = true; }
  }
  let text = accent;
  const away = theme === "light" ? BLACK : WHITE;
  for (let i = 0; i < 40 && contrast(text, PAGE[theme]) < 4.5; i++) text = mix(text, away, 0.08);
  if (text !== accent && !adjusted) adjusted = contrast(accent, PAGE[theme]) < 3;   // the fill itself still reads as a control
  const hover = mix(accent, theme === "light" ? BLACK : WHITE, 0.1);
  const [r, g, b] = rgb(accent);
  return { accent, hover, on, text, tint: `rgba(${r}, ${g}, ${b}, ${theme === "light" ? 0.12 : 0.14})`, adjusted };
}

export function accentSeeds(accent: string): { dark: string; light: string } {
  const c = ACCENTS.find((a) => a.id === accent);
  if (c) return { dark: c.dark, light: c.light };
  const h = HEX.test(accent) ? accent : ACCENTS[0].dark;
  return { dark: h, light: h };
}

/** The stylesheet that applies an appearance: tokens for both themes, fonts, density. */
export function appearanceCss(a: Appearance): string {
  const seeds = accentSeeds(a.accent);
  const block = (t: Theme) => {
    const d = deriveAccent(seeds[t], t);
    return `--accent: ${d.accent}; --accent-hover: ${d.hover}; --on-accent: ${d.on}; --accent-text: ${d.text}; --accent-tint: ${d.tint};`;
  };
  const fonts = `--font-sans: ${FONTS_UI[a.font_ui].stack}; --font-reading: ${FONTS_READING[a.font_reading].stack}; --font-mono: ${FONTS_MONO[a.font_mono].stack};`;
  const large = a.text_size === "large"
    ? "--text-xs: 13px; --text-sm: 14px; --text-md: 15px; --text-lg: 17px; --text-xl: 21px;" : "";
  const compact = a.density === "compact"
    ? "--space-1: 3px; --space-2: 6px; --space-3: 10px; --space-4: 14px;" : "";
  return [
    `:root, [data-theme="dark"] { ${block("dark")} ${fonts} ${large} ${compact} }`,
    `:root[data-theme="light"], [data-theme="light"] { ${block("light")} }`,
  ].join("\n");
}

const STYLE_ID = "vx-appearance";
const LINK_ID = "vx-appearance-fonts";
const CACHE = "vexa.terminal.appearance";

/** Load the web fonts the appearance names (one stylesheet), then repaint the tokens. */
export function applyAppearance(a: Appearance): void {
  if (typeof document === "undefined") return;
  let el = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!el) { el = document.createElement("style"); el.id = STYLE_ID; document.head.appendChild(el); }
  el.textContent = appearanceCss(a);
  const fams = [FONTS_UI[a.font_ui], FONTS_READING[a.font_reading], FONTS_MONO[a.font_mono]]
    .map((f) => ("google" in f ? f.google : "")).filter(Boolean);
  const href = fams.length ? `https://fonts.googleapis.com/css2?${[...new Set(fams)].map((f) => `family=${f}`).join("&")}&display=swap` : "";
  let link = document.getElementById(LINK_ID) as HTMLLinkElement | null;
  if (!href) { link?.remove(); }
  else {
    if (!link) { link = document.createElement("link"); link.id = LINK_ID; link.rel = "stylesheet"; document.head.appendChild(link); }
    if (link.href !== href) link.href = href;
  }
  try { localStorage.setItem(CACHE, JSON.stringify(a)); } catch { /* storage refused */ }
}

/** First frame: the last appearance this browser saw, before the server answers. */
export function applyCachedAppearance(): void {
  try { const raw = localStorage.getItem(CACHE); if (raw) applyAppearance(normalizeAppearance(JSON.parse(raw))); } catch { /* */ }
}

export type AppearanceRead = { user: Partial<Appearance>; company: Partial<Appearance>; effective: Appearance };

export async function readAppearance(): Promise<AppearanceRead | null> {
  try {
    const r = await fetch("/api/user/appearance", { cache: "no-store" });
    if (!r.ok) return null;
    const d = (await r.json()) as { user?: unknown; company?: unknown; effective?: unknown };
    const company = normalizeAppearance(d.company);
    return { user: (d.user ?? {}) as Partial<Appearance>, company: (d.company ?? {}) as Partial<Appearance>, effective: normalizeAppearance(d.effective, company) };
  } catch { return null; }
}

/** Save the person's choice (`null` for a field = back to the company default). */
export async function saveAppearance(change: Partial<Record<keyof Appearance, string | null>>): Promise<AppearanceRead | null> {
  try {
    const r = await fetch("/api/user/appearance", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
    if (!r.ok) return null;
  } catch { return null; }
  return refreshAppearance();
}

/** Re-read and apply — at boot and when the window comes back into view (another device saved). */
export async function refreshAppearance(): Promise<AppearanceRead | null> {
  const got = await readAppearance();
  if (got) applyAppearance(got.effective);
  return got;
}
