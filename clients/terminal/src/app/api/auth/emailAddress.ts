/** The one shape check every sign-in door applies to an email address.
 *
 *  The pattern (something@something.something, no spaces) backtracks on long inputs: matched against
 *  a long run of dots it costs time quadratic in the length, and the terminal is one Node process,
 *  so one oversized value would hold up every request it serves. The length is therefore checked
 *  FIRST, against RFC 5321's limit on a forward path (254 characters), and the pattern only ever sees
 *  something that short. Callers pass the trimmed, lower-cased value they will use.
 */
export const MAX_EMAIL_LENGTH = 254;

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isWellFormedEmail(value: string): boolean {
  return value.length <= MAX_EMAIL_LENGTH && EMAIL_SHAPE.test(value);
}
