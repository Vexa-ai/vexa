/** Read the token stylesheet (`src/app/tokens.css`) into a name → value map per theme, with every
 *  var() reference resolved. Used by the contrast gate and the design guards; pure (takes the CSS text). */

export type ThemeTokens = Record<string, string>;

/** Top-level `:root { … }` blocks (not nested in an at-rule) and the light override block. */
function blocks(css: string): { dark: string[]; light: string[] } {
  const out = { dark: [] as string[], light: [] as string[] };
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let depth = 0, i = 0;
  while (i < clean.length) {
    const open = clean.indexOf("{", i);
    if (open < 0) break;
    const selector = clean.slice(i, open).trim().split(/[;}]/).pop()!.trim();
    // find the matching brace
    let j = open + 1; depth = 1;
    while (j < clean.length && depth > 0) { if (clean[j] === "{") depth++; else if (clean[j] === "}") depth--; j++; }
    const body = clean.slice(open + 1, j - 1);
    if (selector === ":root") out.dark.push(body);
    else if (selector === ':root[data-theme="light"]') out.light.push(body);
    // at-rules (@media …) are skipped whole: reduced-motion overrides are not a theme
    i = j;
  }
  return out;
}

function decls(body: string): ThemeTokens {
  const out: ThemeTokens = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

function resolve(map: ThemeTokens): ThemeTokens {
  const out: ThemeTokens = {};
  const get = (name: string, seen: Set<string>): string => {
    if (seen.has(name)) return map[name] ?? "";
    seen.add(name);
    const v = map[name];
    if (v === undefined) return "";
    return v.replace(/var\((--[\w-]+)(?:\s*,\s*([^)]*))?\)/g, (_, n: string, fb?: string) => {
      const r = get(n, seen);
      return r || (fb ?? "").trim();
    });
  };
  for (const k of Object.keys(map)) out[k] = get(k, new Set());
  return out;
}

export function parseTokens(css: string): { dark: ThemeTokens; light: ThemeTokens; defined: Set<string> } {
  const b = blocks(css);
  const dark = Object.assign({}, ...b.dark.map(decls)) as ThemeTokens;
  const light = { ...dark, ...Object.assign({}, ...b.light.map(decls)) } as ThemeTokens;
  return { dark: resolve(dark), light: resolve(light), defined: new Set([...Object.keys(dark), ...Object.keys(light)]) };
}
