"use client";
/** ExternalLink, Code, SecretReveal (guidelines §4.18, §7 S1–S2).
 *
 *  ExternalLink is the ONE place a link leaves the terminal: `http:` and `https:` only (anything
 *  else — `javascript:`, `data:`, `vbscript:` — renders as plain text), always
 *  `target="_blank" rel="noopener noreferrer"`.
 *
 *  Code is the only primitive (with SecretReveal) allowed to break anywhere: a hash or a key has
 *  no word boundaries.
 *
 *  SecretReveal shows a token, key or invite secret for the one moment it is needed: masked by
 *  default, revealed on an explicit press, with a copy button. The value is NEVER placed in
 *  `title`, `aria-label`, `data-*`, a URL or storage; it exists in the DOM only as the revealed
 *  text node. */
import { useState, type AnchorHTMLAttributes, type ReactNode } from "react";
import { Check, Copy, Eye, EyeOff } from "lucide-react";
import { IconButton } from "./Button";

const SAFE = /^https?:\/\//i;
export const isSafeHref = (href: string): boolean => SAFE.test(href.trim());

export function ExternalLink({ href, children, className, ...rest }: { href: string; children: ReactNode; className?: string }
  & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href" | "target" | "rel" | "children" | "className">) {
  if (!isSafeHref(href)) return <span className={["vx-link-refused", className].filter(Boolean).join(" ")}>{children}</span>;
  return <a {...rest} className={["vx-link", className].filter(Boolean).join(" ")} href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
}

export function Code({ children, block }: { children: string; block?: boolean }) {
  return block ? <pre className="vx-code-block"><code>{children}</code></pre> : <code className="vx-code">{children}</code>;
}

export function SecretReveal({ value, label, onCopy }: {
  value: string; label: string; onCopy?: (v: string) => void | Promise<void>;
}) {
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await (onCopy ? onCopy(value) : navigator.clipboard.writeText(value)); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* the copy button stays; the reader can select the revealed text */ }
  };
  return (
    <span className="vx-secret" role="group" aria-label={label}>
      <code className="vx-code vx-secret-value">{shown ? value : "•".repeat(Math.min(24, Math.max(8, value.length)))}</code>
      <IconButton label={shown ? `Hide ${label}` : `Show ${label}`} size="xs" pressed={shown} onClick={() => setShown((v) => !v)}>
        {shown ? <EyeOff size={14} strokeWidth={1.75} /> : <Eye size={14} strokeWidth={1.75} />}
      </IconButton>
      <IconButton label={copied ? "Copied" : `Copy ${label}`} size="xs" onClick={() => void copy()}>
        {copied ? <Check size={14} strokeWidth={1.75} /> : <Copy size={14} strokeWidth={1.75} />}
      </IconButton>
    </span>
  );
}
