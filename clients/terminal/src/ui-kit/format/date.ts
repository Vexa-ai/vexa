/** The terminal's ONE date formatter (guidelines §5.3), on `Intl.DateTimeFormat` with the reader's
 *  locale and time zone.
 *
 *  · Within 7 days, relative: "6:29 PM" today, "Yesterday 6:29 PM", "Thu".
 *  · Beyond that, absolute: "Oct 8"; the year only when it is not this year ("Oct 8, 2025").
 *  · A DATE-ONLY value ("2026-10-09", the way a page's properties carry a date) has no time to
 *    show, so it is always the absolute day, never "12:00 AM".
 *  · Full precision belongs in the tooltip and `dateTime` (`fullDate`).
 *
 *  Pure: `now`, `locale` and `timeZone` are parameters so tests can pin them. */

export type DateInput = Date | string | number;
export type DateOpts = { now?: Date; locale?: string; timeZone?: string };

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Parse what pages and APIs hand us. A date-only string is midday UTC of that day, so no time zone
 *  moves it across a day boundary. `null` for anything unparseable. */
export function parseDate(v: DateInput): { date: Date; dateOnly: boolean } | null {
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : { date: v, dateOnly: false };
  if (typeof v === "number") { const d = new Date(v); return Number.isNaN(d.getTime()) ? null : { date: d, dateOnly: false }; }
  const s = String(v).trim();
  if (DATE_ONLY.test(s)) { const d = new Date(`${s}T12:00:00Z`); return Number.isNaN(d.getTime()) ? null : { date: d, dateOnly: true }; }
  if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s)) return null;   // only ISO-shaped strings are dates
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : { date: d, dateOnly: false };
}

/** Is this string a date this formatter will render (ISO date or date-time)? */
export const looksLikeDate = (s: string): boolean => parseDate(s) !== null;

/** Calendar-day key of an instant in a time zone, e.g. "2026-10-09". */
function dayKey(d: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}
function dayDiff(a: Date, b: Date, timeZone?: string): number {
  const ka = Date.parse(dayKey(a, timeZone) + "T00:00:00Z");
  const kb = Date.parse(dayKey(b, timeZone) + "T00:00:00Z");
  return Math.round((kb - ka) / 86_400_000);
}

export function formatDate(v: DateInput, opts: DateOpts = {}): string {
  const p = parseDate(v);
  if (!p) return String(v);
  const now = opts.now ?? new Date();
  const { locale } = opts;
  // A date-only value is a calendar day: format it in UTC so the day never shifts.
  const tz = p.dateOnly ? "UTC" : opts.timeZone;
  const sameYear = dayKey(p.date, tz).slice(0, 4) === dayKey(now, opts.timeZone).slice(0, 4);
  if (!p.dateOnly) {
    const ago = dayDiff(p.date, now, tz);   // >0 = in the past
    const time = new Intl.DateTimeFormat(locale, { timeZone: tz, hour: "numeric", minute: "2-digit" }).format(p.date);
    if (ago === 0) return time;
    if (ago === 1) return `Yesterday ${time}`;
    if (ago > 1 && ago < 7) return new Intl.DateTimeFormat(locale, { timeZone: tz, weekday: "short" }).format(p.date);
  }
  return new Intl.DateTimeFormat(locale, {
    timeZone: tz, month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }),
  }).format(p.date);
}

/** Full precision, for the tooltip: "Thu, Oct 8, 2026, 14:32 GMT+1" (or the day alone). */
export function fullDate(v: DateInput, opts: DateOpts = {}): string {
  const p = parseDate(v);
  if (!p) return String(v);
  if (p.dateOnly) {
    return new Intl.DateTimeFormat(opts.locale, { timeZone: "UTC", weekday: "short", year: "numeric", month: "short", day: "numeric" }).format(p.date);
  }
  return new Intl.DateTimeFormat(opts.locale, {
    timeZone: opts.timeZone, weekday: "short", year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  }).format(p.date);
}

/** The machine-readable value for `<time dateTime>`. */
export function isoDate(v: DateInput): string {
  const p = parseDate(v);
  if (!p) return "";
  return p.dateOnly ? p.date.toISOString().slice(0, 10) : p.date.toISOString();
}
