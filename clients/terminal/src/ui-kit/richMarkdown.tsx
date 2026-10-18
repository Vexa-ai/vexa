"use client";
/** RICHER PAGES, SAME MARKDOWN (#1783; founder, on a contact page of plain bullets: "we need richer
 *  formatting and interactive and navigation links"). Pages stay markdown in git; this module only
 *  reads more of it, before the MDX compile, and supplies the components it reads into:
 *
 *  - CALLOUTS: `> [!note] Title` / `[!tip]` / `[!warning]` / `[!important]` blocks → the Note and
 *    Warning boxes the renderer already has;
 *  - TASKS: `- [ ] …` items render as checkboxes that write back to the file (the commit is the
 *    reader's, through the workspace write route), and `TBD: …` lines under a Follow-ups / Next steps
 *    / To-do heading are open tasks too (checking one writes `- [x] …`). Each task has **Ask agent**,
 *    which sends the task and the page to the chat;
 *  - HEADINGS get anchors, and a long page an "On this page" outline;
 *  - DATES (`2026-10-09`) read relatively ("3 days ago"), exact on hover.
 *
 *  In a chat reply (no page behind it) the tasks are shown, not writable. */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { DocMetaContext } from "./docRefs";
import { ASK_CHAT_EVENT } from "../platform/events";

export type TaskInfo = { n: number; line: string; occurrence: number; text: string; done: boolean; kind: "box" | "tbd" };
export type Heading = { level: number; text: string; id: string };

const FENCE = /^\s*(```|~~~)/;
const TASK = /^(\s*)([-*+]|\d+[.)])\s+\[( |x|X)\]\s+(.*)$/;
const TBD = /^(\s*)(?:[-*+]\s+)?TBD:\s*(.+)$/i;
const FOLLOW_UPS = /follow[\s-]*ups?|next\s+steps?|to-?dos?|action\s+items?|open\s+items?/i;
const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const CALLOUT = /^>\s*\[!(\w+)\]\s*(.*)$/;
const ISO_DATE = /(^|[^\w/.\-:])(\d{4}-\d{2}-\d{2})(?![\w/.\-:]|T\d)/g;

export const headingId = (text: string): string =>
  "h-" + text.toLowerCase().replace(/<[^>]*>/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

const cleanAttr = (s: string) => s.replace(/["\\{}<>]/g, "").trim();

/** Dates in the prose of one line, leaving code, wikilinks and link targets alone. */
function datesIn(line: string): string {
  return line.split(/(`[^`]*`|\[\[[^\]]*\]\]|\]\([^)]*\)|<[^>]*>)/g)
    .map((part, i) => (i % 2 ? part : part.replace(ISO_DATE, (_m, pre: string, iso: string) => `${pre}<RelDate iso="${iso}" />`)))
    .join("");
}

/** Read the richer constructs out of one stretch of markdown; returns the source to compile and
 *  what the components need (the tasks, in order). Fenced code is never touched. */
export function enrichMarkdown(src: string): { src: string; tasks: TaskInfo[]; headings: Heading[] } {
  const lines = src.split("\n");
  const out: string[] = [];
  const tasks: TaskInfo[] = [];
  const headings: Heading[] = [];
  const seen = new Map<string, number>();
  let inFence = false;
  let underFollowUps = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE.test(line)) { inFence = !inFence; out.push(line); continue; }
    if (inFence) { out.push(line); continue; }
    const occurrence = seen.get(line) ?? 0;
    seen.set(line, occurrence + 1);
    const h = HEADING.exec(line);
    if (h) {
      underFollowUps = FOLLOW_UPS.test(h[2]);
      headings.push({ level: h[1].length, text: h[2].replace(/\[\[|\]\]|[*_`]/g, ""), id: headingId(h[2].replace(/\[\[|\]\]|[*_`]/g, "")) });
      out.push(line);      // a heading keeps its exact date: it is often the page's own name
      continue;
    }
    const c = CALLOUT.exec(line);
    if (c) {
      const body: string[] = [];
      while (i + 1 < lines.length && /^>/.test(lines[i + 1])) { i++; body.push(lines[i].replace(/^>\s?/, "")); }
      const tone = /^(warning|caution|danger|important|attention|error)$/i.test(c[1]) ? "Warning" : "Note";
      out.push("", `<${tone} title="${cleanAttr(c[2] || c[1][0].toUpperCase() + c[1].slice(1).toLowerCase())}">`, "",
        ...body.map(datesIn), "", `</${tone}>`, "");
      continue;
    }
    const t = TASK.exec(line);
    if (t) {
      const n = tasks.length;
      tasks.push({ n, line, occurrence, text: t[4], done: t[3].toLowerCase() === "x", kind: "box" });
      out.push(`${t[1]}${t[2]} <Task n={${n}} />${datesIn(t[4])} <TaskAsk n={${n}} />`);
      continue;
    }
    const tbd = underFollowUps ? TBD.exec(line) : null;
    if (tbd) {
      const n = tasks.length;
      tasks.push({ n, line, occurrence, text: tbd[2], done: false, kind: "tbd" });
      out.push(`${tbd[1]}- <Task n={${n}} />${datesIn(tbd[2])} <TaskAsk n={${n}} />`);
      continue;
    }
    out.push(datesIn(line));
  }
  return { src: out.join("\n"), tasks, headings };
}

/** The file with one task flipped: a box `[ ]`↔`[x]`, a `TBD:` line → `- [x] …`. The line is found
 *  by its exact text and its occurrence among identical lines; `null` when it is no longer there. */
export function toggleTaskInFile(file: string, task: TaskInfo, done: boolean): string | null {
  const lines = file.split("\n");
  let seen = 0;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i] !== task.line) continue;
    if (seen++ !== task.occurrence) continue;
    if (task.kind === "box") {
      lines[i] = lines[i].replace(/\[( |x|X)\]/, done ? "[x]" : "[ ]");
    } else {
      const m = TBD.exec(lines[i]);
      if (!m) return null;
      lines[i] = `${m[1]}- [${done ? "x" : " "}] ${m[2]}`;
    }
    return lines.join("\n");
  }
  return null;
}

type TaskApi = { tasks: TaskInfo[]; toggle: (t: TaskInfo, done: boolean) => Promise<void>; writable: boolean };
export const TaskContext = createContext<TaskApi>({ tasks: [], toggle: async () => {}, writable: false });

/** The tasks of one stretch of markdown, wired to the page's file when there is one. */
export function useTaskApi(tasks: TaskInfo[]): TaskApi {
  const meta = useContext(DocMetaContext);
  const writable = !!meta.path;
  return {
    tasks, writable,
    toggle: async (t, done) => {
      if (!meta.path) return;
      const api = await import("../data/workspaceApi");
      const file = await api.readWorkspaceFile(meta.path, meta.slug ? { slug: meta.slug } : undefined);
      if (file == null) throw new Error("The page could not be read.");
      const next = toggleTaskInFile(file, t, done);
      if (next == null) throw new Error("The task changed since the page was opened. Reload it.");
      await api.writeWorkspaceFile(meta.path, next, meta.slug ? { slug: meta.slug } : undefined);
    },
  };
}

export function Task({ n }: { n: number }) {
  const api = useContext(TaskContext);
  const t = api.tasks[n];
  const [done, setDone] = useState<boolean>(!!t?.done);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { setDone(!!t?.done); }, [t?.done, t?.line]);
  if (!t) return null;
  return (
    <span className="vx-task" data-task={t.kind} data-done={done ? "" : undefined}>
      <input type="checkbox" className="vx-task-box" checked={done} disabled={!api.writable || busy}
        aria-label={`${done ? "Reopen" : "Done"}: ${t.text}`}
        title={api.writable ? undefined : "Open the page to tick this off"}
        onChange={async (e) => {
          const next = e.currentTarget.checked;
          setDone(next); setBusy(true); setErr("");
          try { await api.toggle(t, next); }
          catch (x) { setDone(!next); setErr(x instanceof Error ? x.message : "Couldn't save."); }
          finally { setBusy(false); }
        }} />
      {err && <span role="alert" className="vx-task-err">{err}</span>}
    </span>
  );
}

/** ASK AGENT about one task: the task and the page it is on, sent to the chat as the person's ask. */
export function TaskAsk({ n }: { n: number }) {
  const api = useContext(TaskContext);
  const meta = useContext(DocMetaContext);
  const t = api.tasks[n];
  if (!t) return null;
  const where = meta.path ? ` (on ${meta.slug ? `${meta.slug}:` : ""}${meta.path})` : "";
  return (
    <button type="button" className="vx-task-ask" title="Ask the agent to help with this task"
      onClick={() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: `Help me with this task${where}: ${t.text}` } }))}>
      Ask agent
    </button>
  );
}

const DAY = 86_400_000;
/** "today" · "yesterday" · "in 3 days" · "2 weeks ago" · a date past a few months. */
export function relativeDay(iso: string, now: number = Date.now()): string {
  const d = Date.parse(`${iso}T00:00:00`);
  if (!Number.isFinite(d)) return iso;
  const today = new Date(now); today.setHours(0, 0, 0, 0);
  const days = Math.round((d - today.getTime()) / DAY);
  if (days === 0) return "today";
  if (days === -1) return "yesterday";
  if (days === 1) return "tomorrow";
  const abs = Math.abs(days);
  const say = (n: number, u: string) => `${n} ${u}${n === 1 ? "" : "s"}`;
  const span = abs < 14 ? say(abs, "day") : abs < 60 ? say(Math.round(abs / 7), "week") : abs < 330 ? say(Math.round(abs / 30), "month") : null;
  if (!span) return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(d));
  return days < 0 ? `${span} ago` : `in ${span}`;
}

export function RelDate({ iso }: { iso: string }) {
  const d = Date.parse(`${iso}T00:00:00`);
  const exact = Number.isFinite(d) ? new Intl.DateTimeFormat(undefined, { weekday: "short", month: "long", day: "numeric", year: "numeric" }).format(new Date(d)) : iso;
  return <time className="vx-reldate" dateTime={iso} title={exact}>{relativeDay(iso)}</time>;
}

/** The text of a heading's children, for its anchor. */
export function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  const props = (node as { props?: { children?: ReactNode; title?: string; label?: string } }).props;
  return props ? (props.title ?? props.label ?? textOf(props.children)) : "";
}

/** "On this page" for a long page: shown when it has four or more section headings. */
export function Outline({ headings }: { headings: Heading[] }) {
  const sections = headings.filter((h) => h.level >= 2 && h.level <= 3);
  const [open, setOpen] = useState(false);
  if (sections.length < 4) return null;
  return (
    <nav className="vx-outline" aria-label="On this page">
      <button type="button" className="vx-outline-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        On this page · {sections.length}
      </button>
      {open && <ul>
        {sections.map((h) => (
          <li key={h.id} data-level={h.level}>
            <a href={`#${h.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(h.id)?.scrollIntoView({ block: "start", behavior: "smooth" }); }}>{h.text}</a>
          </li>
        ))}
      </ul>}
    </nav>
  );
}
