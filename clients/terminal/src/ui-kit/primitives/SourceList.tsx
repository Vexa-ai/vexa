"use client";
/** SourceList (guidelines §4.17) — an agent's sources as rows: title (a safe external link),
 *  host · date, an optional two-line snippet. The date is shown ONCE: a date already written into
 *  the title is not repeated beside it. The first three show; "Show N more" reveals the rest. Fed
 *  structured data `{ title, url, date?, snippet? }`. */
import { useState } from "react";
import { DateText } from "./DateText";
import { ExternalLink } from "./Links";
import { displayUrl } from "./Truncate";
import { isoDate } from "../format/date";

export type Source = { title: string; url: string; date?: string; snippet?: string };

/** Does the title already carry this date (as written: ISO, or the formatter's own words)? */
export function titleHasDate(title: string, date?: string): boolean {
  if (!date) return false;
  const iso = isoDate(date).slice(0, 10);
  return !!iso && title.includes(iso);
}

export function SourceList({ sources, initial = 3 }: { sources: Source[]; initial?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? sources : sources.slice(0, initial);
  return (
    <div className="vx-sources">
      <ol className="vx-sources-list">
        {shown.map((s, i) => {
          const host = displayUrl(s.url).split("/")[0];
          return (
            <li key={`${s.url}-${i}`} className="vx-source">
              <ExternalLink href={s.url}><span className="vx-source-title">{s.title}</span></ExternalLink>
              <span className="vx-source-meta">{host}{s.date && !titleHasDate(s.title, s.date) && <> · <DateText value={s.date} /></>}</span>
              {s.snippet && <span className="vx-source-snippet">{s.snippet}</span>}
            </li>
          );
        })}
      </ol>
      {sources.length > initial && (
        <button type="button" className="vx-kv-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show ${sources.length - initial} more`}
        </button>
      )}
    </div>
  );
}
