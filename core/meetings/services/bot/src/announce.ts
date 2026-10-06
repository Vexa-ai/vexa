/**
 * The one in-meeting chat line the bot posts before leaving at an owner-set duration bound
 * (`leaveAfterMs` → `user_limit_reached`). Pure text construction so the en/ar copies are
 * unit-testable offline; the send itself is the JoinDriver's `announce` port.
 */

/** The announcement body in the invocation's language. Arabic when `language` starts with
 *  `ar`, English otherwise — an absent/unknown language falls back to English so a missing
 *  field never blocks the line. */
export function leaveAfterAnnouncement(language: string | null | undefined, limitMs: number): string {
  const minutes = Math.max(1, Math.round(limitMs / 60_000));
  if ((language ?? '').trim().toLowerCase().startsWith('ar')) {
    return `يغادر مُدوِّن ZAKI: تم بلوغ حدّ ${minutes} دقيقة. ستكون الملاحظات جاهزةً قريبًا.`;
  }
  return `ZAKI notetaker is leaving: the ${minutes}-minute limit was reached. Notes will be ready shortly.`;
}
