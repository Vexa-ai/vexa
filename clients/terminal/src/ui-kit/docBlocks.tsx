"use client";
/** docBlocks — the Mintlify components a workspace page may use, drawn with the kit.
 *
 *  ONE set, registered by MdxDoc (the compiled page) and drawn by the plain-Markdown fallback too,
 *  so a page looks the same whether or not it compiled. The vocabulary is closed (`DOC_COMPONENTS`
 *  in ./docComponents); an unknown component renders as its inner content, never as a tag.
 *
 *  Design (UI-DECISIONS 7r/7x, guidelines §2.2): no solid fills anywhere — a callout is a tinted
 *  left rule over the page, a step's number is an outlined disc, a checked task is an outlined box
 *  with an accent tick. Tabs are the kit's `Tabs`; an accordion is a native `<details>`. Every look
 *  lives in `primitives/prose.css` on tokens; nothing here writes a colour inline. */
import { Children, isValidElement, useState, type ReactNode } from "react";
import { AlertTriangle, Check as CheckIcon, CheckCircle2, Info as InfoIcon, Lightbulb, OctagonAlert } from "lucide-react";
import { Tabs as KitTabs } from "./primitives/Tabs";

type Tone = "info" | "warning" | "tip" | "success" | "danger" | "neutral";
const TONE_ICON: Record<Tone, ReactNode> = {
  info: <InfoIcon size={14} aria-hidden />, warning: <AlertTriangle size={14} aria-hidden />, tip: <Lightbulb size={14} aria-hidden />,
  success: <CheckCircle2 size={14} aria-hidden />, danger: <OctagonAlert size={14} aria-hidden />, neutral: <InfoIcon size={14} aria-hidden />,
};

export function DocCallout({ tone, title, children }: { tone: Tone; title?: string; children?: ReactNode }) {
  return (
    <div className="vx-callout vx-doc-callout" data-tone={tone} role="note">
      <span className="vx-doc-callout-icon">{TONE_ICON[tone]}</span>
      <div className="vx-doc-callout-body">{title && <div className="vx-callout-title">{title}</div>}{children}</div>
    </div>
  );
}
type CalloutProps = { title?: string; children?: ReactNode };
export const Note = (p: CalloutProps) => <DocCallout tone="info" {...p} />;
export const Info = (p: CalloutProps) => <DocCallout tone="info" {...p} />;
export const Warning = (p: CalloutProps) => <DocCallout tone="warning" {...p} />;
export const Tip = (p: CalloutProps) => <DocCallout tone="tip" {...p} />;
export const Check = (p: CalloutProps) => <DocCallout tone="success" {...p} />;
export const Danger = (p: CalloutProps) => <DocCallout tone="danger" {...p} />;
export const Callout = (p: CalloutProps) => <DocCallout tone="neutral" {...p} />;

/** Only the element children (MDX hands over the newlines between tags as strings). */
const elements = (children: ReactNode) => Children.toArray(children).filter((c) => isValidElement(c) || (typeof c === "string" && c.trim()));

export function Steps({ children }: { children?: ReactNode }) {
  const items = elements(children);
  return (
    <ol className="vx-doc-steps">
      {items.map((child, i) => (
        <li key={i} className="vx-doc-step">
          <span className="vx-doc-step-rail" aria-hidden><span className="vx-doc-step-n">{i + 1}</span></span>
          <div className="vx-doc-step-body">{child}</div>
        </li>
      ))}
    </ol>
  );
}

export function Step({ title, children }: { title?: string; children?: ReactNode }) {
  return (
    <div>
      {title && <div className="vx-doc-step-title">{title}</div>}
      <div className="vx-doc-step-content">{children}</div>
    </div>
  );
}

type Titled = { props?: { title?: string; children?: ReactNode } };
export function Tabs({ children }: { children?: ReactNode }) {
  const items = elements(children) as Titled[];
  const [active, setActive] = useState("0");
  if (!items.length) return null;
  const current = items[Number(active)] ?? items[0];
  return (
    <div className="vx-doc-tabs">
      <KitTabs label="Tabs" value={active} onChange={setActive}
        items={items.map((t, i) => ({ key: String(i), label: t.props?.title ?? `Tab ${i + 1}` }))} />
      <div className="vx-doc-tabs-panel" role="tabpanel">{current?.props?.children}</div>
    </div>
  );
}
export const Tab = ({ children }: { title?: string; children?: ReactNode }) => <>{children}</>;

/** A fence's language, read from the `<pre><code class="language-x">` MDX makes of it. */
function fenceLang(node: unknown): string {
  if (!isValidElement(node)) return "";
  const kids = Children.toArray((node.props as { children?: ReactNode }).children);
  const code = kids.find(isValidElement);
  const cls = code && isValidElement(code) ? (code.props as { className?: string | string[] }).className : undefined;
  const s = Array.isArray(cls) ? cls.join(" ") : cls ?? "";
  return /(?:^|\s)language-([\w-]+)/.exec(s)?.[1] ?? "";
}

/** Several fences, one shown at a time; a tab is named by the fence's title or language. */
export function CodeGroup({ children, items }: { children?: ReactNode; items?: { label: string; node: ReactNode }[] }) {
  const list = items ?? elements(children).map((c, i) => ({ label: fenceLang(c) || `Code ${i + 1}`, node: c as ReactNode }));
  const [active, setActive] = useState("0");
  if (list.length <= 1) return <div className="vx-doc-codegroup">{list[0]?.node}</div>;
  return (
    <div className="vx-doc-codegroup">
      <KitTabs label="Code" value={active} onChange={setActive} items={list.map((it, i) => ({ key: String(i), label: it.label }))} />
      <div role="tabpanel">{(list[Number(active)] ?? list[0]).node}</div>
    </div>
  );
}

export function Accordion({ title, children, defaultOpen }: { title?: string; children?: ReactNode; defaultOpen?: string | boolean }) {
  return (
    <details className="vx-doc-accordion" open={defaultOpen === true || defaultOpen === "true" || undefined}>
      <summary>{title ?? "Details"}</summary>
      <div className="vx-doc-accordion-body">{children}</div>
    </details>
  );
}
export const AccordionGroup = ({ children }: { children?: ReactNode }) => <div className="vx-doc-accordion-group">{children}</div>;

export function Frame({ caption, children }: { caption?: string; children?: ReactNode }) {
  return <figure className="vx-doc-frame">{children}{caption && <figcaption>{caption}</figcaption>}</figure>;
}

export function Columns({ cols, children }: { cols?: string | number; children?: ReactNode }) {
  const kids = elements(children);
  // an empty column (a block whose live list rendered nothing for this reader) leaves no gap
  const shown = kids.filter((c) => !isValidElement(c) || hasContent((c.props as { children?: ReactNode }).children));
  if (!shown.length) return null;
  return <div className="vx-doc-columns" data-cols={String(cols ?? shown.length)}>{shown}</div>;
}
export const Column = ({ children }: { children?: ReactNode }) => <div className="vx-doc-column">{children}</div>;
function hasContent(children: ReactNode): boolean {
  return Children.toArray(children).some((c) => (typeof c === "string" ? c.trim() !== "" : true));
}

/** A read-only task box: outlined, an accent tick when done (7r: no solid fill). */
export function TaskMark({ done, label, decorative = false }: { done: boolean; label?: string; decorative?: boolean }) {
  const a11y = decorative ? { "aria-hidden": true } : { role: "img", "aria-label": `${done ? "Done" : "Open"}${label ? `: ${label}` : ""}` };
  return (
    <span className="vx-checkbox" data-checked={done ? "" : undefined} {...a11y}>
      {done && <CheckIcon size={11} strokeWidth={3} aria-hidden />}
    </span>
  );
}

/** Every component a page may name, by its Mintlify tag. */
export const DOC_BLOCKS = { Note, Info, Warning, Tip, Check, Danger, Callout, Steps, Step, Tabs, Tab, CodeGroup, Accordion, AccordionGroup, Frame, Columns, Column };
