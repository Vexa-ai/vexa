/** STATUS IN HUMAN WORDS (founder 2026-10-16: "failed: no_outcome: the turn did not finish within the
 *  hour" leaked an internal code). A run that never finished says so; any `snake_code:` is dropped. */
export function humanStatus(text: string): string {
  const t = String(text ?? "").trim();
  const m = /^(.*?)\s+failed:\s*no_outcome\b.*$/i.exec(t);
  if (m) return `${m[1]} didn't finish.`;
  return t.replace(/\b[a-z]+(?:_[a-z]+)+:\s*/g, "").replace(/\s{2,}/g, " ").trim();
}
