/** WCAG 2.2 contrast, computed (guidelines §2.1, §6 A1–A2). One function serves the contrast gate
 *  (`tokenContrast.test.ts`) and the catalogue's live ratios (`/design#tokens`), so what the page
 *  shows is what the gate checked.
 *
 *  Colours are `#rgb`, `#rrggbb` or `rgb()/rgba()`. A translucent foreground is blended over the
 *  background first — a border at white 36% IS the colour it paints on that surface. */

export type RGBA = { r: number; g: number; b: number; a: number };

export function parseColor(input: string): RGBA | null {
  const s = input.trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(s);
  if (m) { const [r, g, b] = m[1].split("").map((c) => parseInt(c + c, 16)); return { r, g, b, a: 1 }; }
  m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/.exec(s);
  if (m) {
    const n = m[1];
    return { r: parseInt(n.slice(0, 2), 16), g: parseInt(n.slice(2, 4), 16), b: parseInt(n.slice(4, 6), 16), a: m[2] ? parseInt(m[2], 16) / 255 : 1 };
  }
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+%?)\s*)?\)$/.exec(s);
  if (m) {
    const a = m[4] === undefined ? 1 : m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
    return { r: +m[1], g: +m[2], b: +m[3], a };
  }
  return null;
}

export function blend(fg: RGBA, bg: RGBA): RGBA {
  const a = fg.a;
  return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
}

export function luminance({ r, g, b }: RGBA): number {
  const f = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** The WCAG ratio of `fg` on `bg` (≥ 1). `null` if either colour cannot be parsed. */
export function contrastRatio(fg: string, bg: string): number | null {
  const f = parseColor(fg), b = parseColor(bg);
  if (!f || !b) return null;
  const solidBg = b.a < 1 ? blend(b, { r: 0, g: 0, b: 0, a: 1 }) : b;
  const solidFg = f.a < 1 ? blend(f, solidBg) : f;
  const la = luminance(solidFg), lb = luminance(solidBg);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
