/**
 * global.d.ts — ambient typing for the page-side global the spec reads via page.evaluate.
 *
 * form-entry.tsx records every onSubmit payload onto `window.__submitted`. The Playwright spec reads it
 * back inside page.evaluate callbacks (which are typed against the browser `Window`), so we declare the
 * field here too. Kept loose (`any[]`) — the spec asserts the exact shape against the golden.
 */
export {};

declare global {
  interface Window {
    __submitted: any[];
  }
}
