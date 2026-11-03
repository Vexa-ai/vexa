"use client";
/** Truncate (guidelines §3.4) — a long unbreakable token on ONE line: an ellipsis instead of a
 *  letter-by-letter wrap, the full value in the tooltip and the accessible name.
 *
 *  `end` (default) clips the tail: "name@example-dom…". `middle` keeps both ends, for paths and IDs
 *  where the end is what tells two values apart: "acme…/meetings/2026-10-08.md". Middle truncation
 *  is CSS-only — a shrinking head with its own ellipsis and a tail that never shrinks — so it needs
 *  no measuring and survives any pane width.
 *
 *  `href` renders it as a link; external links get `target=_blank rel="noopener noreferrer"` and
 *  only `http:`/`https:`/`mailto:` schemes (anything else renders as text). */
import type { ReactNode } from "react";

const SAFE = /^(https?:|mailto:)/i;

/** Where to cut a middle-truncated value: the last path segment, else the last 10 characters. */
export function splitForMiddle(text: string): [string, string] {
  const slash = text.lastIndexOf("/");
  if (slash > 0 && slash < text.length - 1 && text.length - slash <= 32) return [text.slice(0, slash), text.slice(slash)];
  const n = Math.min(10, Math.floor(text.length / 3));
  return [text.slice(0, text.length - n), text.slice(text.length - n)];
}

export function Truncate({ text, mode = "end", href, title, children }: {
  text: string; mode?: "end" | "middle"; href?: string; title?: string;
  /** Optional display override (e.g. a URL shown without its scheme); `text` stays the full value. */
  children?: ReactNode;
}) {
  const shown = children ?? text;
  const body = mode === "middle" && typeof shown === "string"
    ? (() => { const [head, tail] = splitForMiddle(shown); return <><span className="vx-trunc-head">{head}</span><span className="vx-trunc-tail">{tail}</span></>; })()
    : <span className="vx-trunc-head">{shown}</span>;
  const common = { className: "vx-trunc", "data-mode": mode, title: title ?? text } as const;
  if (href && SAFE.test(href)) {
    const external = /^https?:/i.test(href);
    return <a {...common} href={href} aria-label={text} {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{body}</a>;
  }
  return <span {...common} aria-label={text}>{body}</span>;
}

/** A URL as people read it: host + path, no scheme, no trailing slash. */
export function displayUrl(url: string): string {
  try {
    const u = new URL(url);
    const path = (u.pathname + u.search).replace(/\/$/, "");
    return u.host + (path === "/" ? "" : path);
  } catch { return url; }
}
