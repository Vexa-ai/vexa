/** The room agent's REASON LINE (R1 2.2, behind `rooms`). An act nobody asked for — a proposal, a
 *  task it started, research it did — begins with one `_because …_` line (founder 2026-10-15: "it acts
 *  only when it adds something, and says why"). The room shows that line quietly above the act;
 *  everything else of the turn renders as before. Only a FIRST line counts. */
const LEAD = /^\s*[_*]((?:b|B)ecause\s[^_*\n]+?)[_*]\s*(?:\n+|$)/;

export function splitBecause(text: string): { because: string | null; body: string } {
  const m = LEAD.exec(text ?? "");
  if (!m) return { because: null, body: text };
  const because = m[1].trim().replace(/^Because/, "because");
  return { because, body: text.slice(m[0].length) };
}
