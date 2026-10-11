"use client";
/** DateText (guidelines §5.3, §4.18) — a date is ONE unbreakable unit. `<time dateTime>` with
 *  `white-space: nowrap`, so "2026-10-09" can never split at its hyphen, rendered by the one
 *  formatter (`ui-kit/format/date.ts`) with full precision in the tooltip. */
import { formatDate, fullDate, isoDate, type DateInput, type DateOpts } from "../format/date";

export function DateText({ value, opts }: { value: DateInput; opts?: DateOpts }) {
  const iso = isoDate(value);
  if (!iso) return <span className="vx-nowrap">{String(value)}</span>;
  return <time className="vx-nowrap vx-tabular" dateTime={iso} title={fullDate(value, opts)}>{formatDate(value, opts)}</time>;
}
