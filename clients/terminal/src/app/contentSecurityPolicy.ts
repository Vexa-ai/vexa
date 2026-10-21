/** The terminal pages' Content-Security-Policy, defence in depth behind the page renderer.
 *
 *  Scripts run only when they carry this response's nonce, or when a script that carried it loaded
 *  them ('strict-dynamic', which covers Next's chunks and next/script). An inline script that came
 *  in with page content has no nonce, so it does not run, and that holds inside an `srcdoc` frame
 *  too, because such a frame inherits this policy. 'unsafe-eval' stays because the MDX renderer
 *  compiles pages in the browser and the canvas runtime evaluates widget code.
 *  Frames are this origin only (the sign-in browser view is served from /api). `base`, `object`
 *  and form targets are closed. Styles, images and connections are not narrowed here. */
export const NONCE_HEADER = "x-nonce";

export function contentSecurityPolicy(nonce: string): string {
  return [
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval'`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join("; ");
}

/** A fresh nonce: 128 random bits, base64. */
export function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}
