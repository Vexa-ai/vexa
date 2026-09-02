/** MINUTES — what a room SHOWS, and which page is in front.
 *
 *  Two decisions, deliberately separated:
 *
 *    the MEETING decides which pages EXIST. There are exactly TWO layouts, not three (founder
 *    ruling): the only distinction that changes the room is whether a TRANSCRIPT EXISTS YET.
 *    `meetingPhase()` still returns prep | live | post — chat.tsx needs all three for its mode
 *    chip — but live and post render the same shape here, because in both a transcript exists
 *    and it belongs on the right. Splitting them over-fits.
 *
 *    the CHAT decides which of them is IN FRONT. `?view=` is not a URL feature: it seeds a chat's
 *    `artifacts[]` — the right-sidebar tabs — and after that the state belongs to the chat. A
 *    deeplink is just a chat constructor. It may focus a page the meeting already has, or add a
 *    file; it may not invent a meeting page that does not exist, and it may not empty the panel.
 *
 *  Both are pure functions on purpose: the shell wires them, the tests read them directly.
 */
import type { MeetingPhase } from "../surfaces/meetingModel";
import { entitySlug, normalizeDocPath } from "../ui-kit/docLinks";
import type { Page } from "./types";
import { artifactKey, type Artifact } from "./chats";

/** The tab a clicked LINK lands on — the whole of what the shell's open-entity listener decides.
 *
 *  It always returns one, and that is the point: the listener used to `return` when the resolver
 *  came back empty, which is exactly the case a reader meets most (an entity the reply names before
 *  anything writes its doc). The click did nothing at all, and the chip carried every visual cue of
 *  being clickable. An unresolved title now opens its CANONICAL path instead, where the panel says
 *  the page is not there yet — a real answer rather than a silence. */
export function pageForDocRef(
  detail: { path?: string; wikilink?: string; slug?: string; docPath?: string },
  resolved?: { path: string; slug?: string } | null,
): Page | null {
  const named = (p: string) => (p.split("/").pop() ?? p).replace(/\.md$/i, "");
  if (resolved) return { path: resolved.path, slug: resolved.slug, label: named(resolved.path) };
  if (detail.path) {
    const path = normalizeDocPath(detail.path, detail.docPath);
    return path ? { path, slug: detail.slug, label: named(path) } : null;
  }
  if (detail.wikilink) {
    const slug = entitySlug(detail.wikilink);
    // no type is knowable for a title nothing has written — `kg/entities/<slug>.md` is the
    // shape without the guess, and the chip's own title stays the tab's name.
    return slug ? { path: `kg/entities/${slug}.md`, slug: detail.slug, label: detail.wikilink } : null;
  }
  return null;
}

/** The doc tab a MEETING ref falls back to when no row in the list matches it — a meeting this
 *  account cannot see, or one the list has not caught up with. The canvas needs a row id to fetch
 *  anything, so there is nothing to render; its notes page is addressable regardless. */
export function pageForMeetingRef(ref: string): Page {
  const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
  return { path: `kg/entities/meeting/${native}.md`, label: native };
}

/** A preset's `tabs:` / `focus:` token → the ARTIFACT it names. PRD decision 18: the link sets the
 *  chat's record, and the right panel renders only from the record — so a preset has to be able to
 *  SAY which documents its conversation is about, in the file's own vocabulary rather than in the
 *  panel's.
 *
 *  Four shapes, in the order they are tried:
 *
 *    `meeting:transcript`  the meeting canvas, bound to the ROW id (live segments, recording)
 *    `meeting:note`        the meeting's own document — "Brief" before it happened, "Minutes"
 *                          after, which is the same file under the name the reader needs today
 *    `<workspace>/<path>`  workspace-qualified, e.g. `_global/PRINCIPLES.md`. The first segment is
 *                          a workspace ONLY when it is one this chat actually mounts — otherwise
 *                          `kg/entities/meeting/x.md` would resolve to a workspace called `kg`.
 *    `<path>`              the reader's own desk.
 *
 *  A meeting token with no meeting in context resolves to NOTHING rather than to a broken tab: a
 *  preset written for a meeting, clicked without one, should open one document fewer, not a page
 *  that can never load. */
export function artifactFromToken(
  token: string,
  ctx: { native?: string | null; meetingId?: string | null; phase?: MeetingPhase | null; mounts?: string[] },
): Artifact | null {
  const t = token.trim();
  if (!t || t.split("/").includes("..")) return null;
  const named = (p: string) => (p.split("/").pop() ?? p).replace(/\.md$/i, "");

  if (/^meeting:transcript$/i.test(t)) {
    return ctx.meetingId ? { kind: "meeting", path: String(ctx.meetingId), label: "Transcript" } : null;
  }
  if (/^meeting:note$/i.test(t)) {
    if (!ctx.native) return null;
    return { path: `kg/entities/meeting/${ctx.native}.md`, label: ctx.phase === "post" ? "Minutes" : "Brief" };
  }
  const slash = t.indexOf("/");
  if (slash > 0) {
    const head = t.slice(0, slash);
    const rest = t.slice(slash + 1);
    // `_global` and `personal` are always workspaces; anything else must be one this chat mounts,
    // so a path that merely LOOKS qualified (`kg/entities/…`) stays a path.
    const isWs = head === "_global" || head === "personal" || (ctx.mounts ?? []).includes(head);
    if (isWs && rest) {
      return head === "personal"
        ? { path: rest, label: named(rest) }
        : { path: rest, slug: head, label: named(rest) };
    }
  }
  return { path: t, label: named(t) };
}

/** A preset's whole `tabs:` list → the chat's opening `artifacts[]`, deduped by identity and with
 *  unresolvable tokens dropped. Order is the preset's: it is the author's reading order. */
export function artifactsFromTokens(
  tokens: string[],
  ctx: { native?: string | null; meetingId?: string | null; phase?: MeetingPhase | null; mounts?: string[] },
): Artifact[] {
  const out: Artifact[] = [];
  const seen = new Set<string>();
  for (const raw of tokens) {
    const a = artifactFromToken(raw, ctx);
    if (!a) continue;
    const k = artifactKey(a);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(a);
  }
  return out;
}

/** THE TAB A WRITTEN FILE LANDS ON (F41) — the `artifact` stream event, resolved to a Page.
 *
 *  `workspace` is "" when the write went to the caller's own desk. That is not a missing value: the
 *  server's record resolved it and said "no slug", exactly as `personal/…` tokens resolve above, so
 *  it becomes `slug: undefined` rather than a guess at which workspace was meant.
 *
 *  Pure and here rather than inline in the shell for the same reason every other token resolver is:
 *  what a path means is a contract with the server, and a contract is worth a test. */
export function pageForArtifact(ev: { workspace?: string; path?: string }): Page | null {
  const path = (ev.path ?? "").trim();
  // A path that walks out of the mount is refused, as everywhere else here — a write we cannot
  // name honestly opens no tab rather than a tab pointing somewhere it should not.
  if (!path || path.split("/").includes("..")) return null;
  const slug = (ev.workspace ?? "").trim();
  return { path, slug: slug || undefined, label: (path.split("/").pop() ?? path).replace(/\.md$/i, "") };
}

/** WHAT AN `artifact` EVENT DOES TO THE OPEN TABS (F41) — the whole decision, as a pure function.
 *
 *  Three rules, and the third is the one worth having a test for:
 *    · the tab is APPENDED, idempotently by artifact key — the same file written twice in a turn is
 *      one tab, not two;
 *    · it comes to the FRONT only when the event says `focus: true`;
 *    · …and never over a focus the READER chose. Someone who has opened a document is reading it,
 *      and an agent's write appears in the strip and waits its turn. This is PRD decision 18's rule
 *      one level down — "a second arrival must not tidy their desk out from under them" — and it is
 *      the same rule whether the second arrival is a re-clicked link or the agent's own write.
 *
 *  `focus` in the result is the page to bring forward, or null for "append only". The caller decides
 *  nothing; it wires this. */
export function artifactTabEffect(
  ev: { workspace?: string; path?: string; focus?: boolean },
  pages: Page[],
  readerChoseFocus: boolean,
): { pages: Page[]; focus: Page | null } | null {
  const pg = pageForArtifact(ev);
  if (!pg) return null;
  const key = artifactKey(pg);
  const already = pages.find((x) => artifactKey(x) === key);
  return {
    pages: already ? pages : [...pages, pg],
    focus: ev.focus === true && !readerChoseFocus ? (already ?? pg) : null,
  };
}

/** Where App.tsx stashes a `?view=` spec — the URL is cleaned on landing, so the value travels here.
 *  What it carries is a chat's opening `artifacts[]`, not a route. */
export const VIEW_KEY = "vexa.composedView";

/** The reader's own page. Present in every room: there is always somewhere to write. */
export const personalPage = (): Page => ({ path: "README.md", label: "Personal page" });

/** The pages a meeting room shows — TWO shapes, keyed on whether a transcript exists.
 *  `native` absent = nothing captured under this row yet, so there is only the personal page.
 *
 *  A TRANSCRIPT IS NOT A FILE (founder ruling 2026-09-01). `kg/entities/meeting/<native>.transcript.md`
 *  was a dead pointer — nothing writes it — and a document that has to be POLLED to look alive was
 *  only ever the mock's trick. The real transcript is the meeting canvas the workbench already
 *  registers: it fetches by row id and streams live segments over the copilot subscription, so it
 *  is live-aware by construction rather than by a 2.5s timer.
 *
 *  `meetingId` is the ROW ID that canvas binds to. Absent = there is no row behind this meeting,
 *  which today means exactly one thing: a `?mock=1` fixture. Those keep the canned markdown page,
 *  because a fabricated meeting has nothing for the canvas to fetch. Brief and Minutes are
 *  documents in both cases — they are real files the agent writes. */
export function pagesForPhase(phase: MeetingPhase, native?: string | null, meetingId?: string | null): Page[] {
  if (!native) return [personalPage()];
  const doc = `kg/entities/meeting/${native}.md`;
  // prep: no transcript yet. The one page that matters before the room — what you walk in to decide.
  if (phase === "prep") return [{ path: doc, label: "Brief" }, personalPage()];
  // live or post: a transcript exists and it leads. The meeting doc is the SAME file either way —
  // it is the brief while the room is running and the minutes once it is not, so only its name moves.
  const transcript: Page = meetingId
    ? { kind: "meeting", path: meetingId, label: "Transcript" }
    : { path: `kg/entities/meeting/${native}.transcript.md`, label: "Transcript" };
  return [
    transcript,
    { path: doc, label: phase === "post" ? "Minutes" : "Brief" },
    personalPage(),
  ];
}

/** A label reduced to the words a link may name: "Transcript · live" → ["transcript", "live"]. */
const words = (label: string) => label.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/** Does `name` name this page? The label's FIRST word is its name; anything after is qualifier —
 *  which is why `transcript` finds both "Transcript" and "Transcript · live". */
export function labelMatches(label: string, name: string): boolean {
  const w = words(label);
  return w.length > 0 && w[0] === name;
}

export interface ResolvedView {
  /** the chat's opening artifacts — the meeting's own pages, plus anything `file:` added. */
  pages: Page[];
  /** the artifact the link asked to open, or null — null means "keep the room's default". */
  focus: Page | null;
}

/** Resolve a `?view=` spec into a chat's opening `artifacts[]`.
 *
 *  `transcript` | `minutes` | `brief` — focus that page (matched on the label the room produced)
 *  `file:<path>`                      — add that page and focus it
 *  comma-separated; the last token that RESOLVES keeps focus. Unresolvable names are ignored, so
 *  a stale link degrades to the room's own layout instead of an empty panel. */
export function resolveView(spec: string | null | undefined, phasePages: Page[]): ResolvedView {
  const pages = [...phasePages];
  let focus: Page | null = null;
  for (const raw of (spec ?? "").split(",")) {
    const token = raw.trim();
    if (!token) continue;
    if (/^file:/i.test(token)) {
      const path = token.slice(5).trim().replace(/^\/+/, "");
      // a link never walks out of the mount, and never names an absolute host path
      if (!path || path.split("/").includes("..")) continue;
      // `!pg.kind` — a meeting tab's `path` is a row id, and a `file:` token must never resolve
      // onto it however numerically alike they look.
      const already = pages.find((pg) => pg.path === path && !pg.slug && !pg.kind);
      if (already) { focus = already; continue; }
      const added: Page = { path, label: (path.split("/").pop() ?? path).replace(/\.md$/i, "") };
      pages.push(added);
      focus = added;
      continue;
    }
    const hit = pages.find((pg) => labelMatches(pg.label, token.toLowerCase()));
    if (hit) focus = hit;   // no hit → ignored on purpose; the default survives
  }
  return { pages, focus };
}

// ── THE VIEW SLOT — a click NAVIGATES, it does not collect (PRD decision 28) ─────────────────────
/** A file clicked in the panel's navigator moves the panel's SINGLE view; it never mints a tab.
 *  Tabs are minted only by an explicit open-in-tab (middle-click, the row's ⧉) or by a scaffold.
 *
 *  STUB. Branch `panel-view-slot` owns the real thing — `view: {workspace, path}` on the chat
 *  record, with back/forward over it. What lives here is only the SEAM, so the navigator can be
 *  written against the final call and nothing has to be rewritten when the slot lands: the click
 *  ANNOUNCES a destination, the shell puts it in front, and the navigator keeps no state about
 *  what is being read. On the rebase this block goes and `navigateView` resolves to theirs.
 *
 *  Deliberately an event and not a callback prop: it is the same shape as OPEN_ENTITY_EVENT and
 *  ARTIFACT_EVENT, which is how every other "something wants to be in front" already reaches the
 *  shell — one route, not two.
 */
export const VIEW_NAVIGATE_EVENT = "vexa:view-navigate";

export interface ViewSlot { workspace?: string; path: string; label: string }

/** The slot a workspace + path address. `workspace` empty ⇒ the reader's own desk (no slug), the
 *  same "" ⇒ undefined rule `artifactFromDocRef` applies — an absent slug is a resolved answer. */
export function viewSlotFor(workspace: string | undefined, path: string): ViewSlot {
  const clean = String(path ?? "").replace(/^\/+/, "");
  return {
    workspace: workspace || undefined,
    path: clean,
    label: (clean.split("/").pop() ?? clean).replace(/\.md$/i, ""),
  };
}

/** Put a file in front. A path that walks out of its mount is refused here rather than at the
 *  fetch, exactly as `resolveView` refuses one from a link. */
export function navigateView(workspace: string | undefined, path: string): void {
  const detail = viewSlotFor(workspace, path);
  if (!detail.path || detail.path.split("/").includes("..")) return;
  window.dispatchEvent(new CustomEvent(VIEW_NAVIGATE_EVENT, { detail }));
}
