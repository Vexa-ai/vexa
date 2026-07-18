// A leave-button matcher for the BROWSER context. `css` is a plain-CSS scope
// (evaluated with document.querySelector inside page.evaluate, where Playwright
// engines like `:has-text()` do not exist); `text` filters those candidates by
// whitespace-normalized, case-insensitive substring of textContent (the
// semantics `:has-text()` applies in Playwright contexts). At least one field
// must be present; `css` alone means "first visible match", `text` alone scopes
// to any button-like element.
export type BrowserContextButtonMatcher = { css?: string; text?: string };

// The leave click that runs INSIDE the page (shipped through page.evaluate by
// every platform's consumers — ONE canonical routine, shared across googlemeet
// and msteams, so an injected hook and a direct leave can never drift, and each
// platform's no-browser fixture test drives exactly the function production
// serializes into the browser).
//
// Self-contained by contract: it is serialized into the browser context, where
// module scope does not exist — DOM globals and its argument only.
// document.querySelector understands plain CSS (no Playwright engines), so
// text-labelled buttons are expressed as `text` fields and matched here by
// whitespace-normalized, case-insensitive substring of textContent — the
// semantics `:has-text()` applies in Playwright contexts. Matchers are tried
// in order; the first visible match is clicked.
export async function leaveBrowserClick(
  matchers: BrowserContextButtonMatcher[],
): Promise<boolean> {
  // Serialization contract: esbuild-family compilers (tsx — the debug harness
  // lane) emit nested function expressions wrapped in a `__name` helper that
  // does not exist inside the page. Define an identity fallback BEFORE any
  // nested function is created, so the serialized source is self-contained
  // under every compiler (tsc emits none of this; the line is then inert).
  (globalThis as any).__name = (globalThis as any).__name || ((f: unknown) => f);
  const blog = (m: string) => { try { (window as any).logBot?.(m); } catch { /* logging is best-effort */ } };
  const normalize = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  const isVisible = (el: Element) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el as HTMLElement);
    return rect.width > 0 && rect.height > 0
      && cs.display !== "none" && cs.visibility !== "hidden" && cs.opacity !== "0";
  };
  for (const matcher of matchers) {
    const scope = matcher.css ?? 'button, [role="button"]';
    let candidates: Element[];
    try {
      candidates = Array.from(document.querySelectorAll(scope));
    } catch (e: any) {
      // The selector-validity gate CSS-parses every declared browser-context
      // entry, so this only fires on drift — loudly, never silently.
      blog(`[leave] selector failed in browser context: ${scope} — ${e?.message}`);
      continue;
    }
    const needle = matcher.text === undefined ? null : normalize(matcher.text);
    const button = candidates.find(
      (el) => (needle === null || normalize(el.textContent || "").includes(needle)) && isVisible(el),
    ) as HTMLElement | undefined;
    if (!button) continue;
    button.scrollIntoView({ behavior: "smooth", block: "center" });
    await new Promise((r) => setTimeout(r, 300));
    button.click();
    await new Promise((r) => setTimeout(r, 800));
    const via = [matcher.css, matcher.text === undefined ? undefined : `text~"${matcher.text}"`]
      .filter(Boolean).join(" ");
    blog(`[leave] clicked leave button via ${via}`);
    return true;
  }
  blog("[leave] no visible leave button matched any matcher");
  return false;
}
