/**
 * Email address format check used before any registration or magic-link work.
 *
 * Accepts exactly what the previous `^[^\s@]+@[^\s@]+\.[^\s@]+$` pattern
 * accepted — one "@", no whitespace, a non-empty local part, and a domain with
 * a dot that is neither its first nor its last character — but parses the
 * value in a single linear pass and caps its length at the RFC 5321 maximum.
 */
export const MAX_EMAIL_LENGTH = 254;

const WHITESPACE = /\s/;

export function isValidEmailFormat(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (value.length === 0 || value.length > MAX_EMAIL_LENGTH) return false;
  if (WHITESPACE.test(value)) return false;

  const at = value.indexOf("@");
  if (at <= 0 || at !== value.lastIndexOf("@")) return false;

  const domain = value.slice(at + 1);
  // A dot with at least one character on each side of it.
  return domain.length >= 3 && domain.slice(1, -1).includes(".");
}
