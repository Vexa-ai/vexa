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
import { artifactKey, orderHistory, type Artifact } from "./chats";

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
 *                          after, which is the same file under the name the reader needs today.
 *                          Its PATH comes from the scaffold (`refs.note_path`), never from a
 *                          shape assembled here; without one the token drops.
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
  ctx: {
    native?: string | null; notePath?: string | null; meetingId?: string | null;
    phase?: MeetingPhase | null; mounts?: string[];
  },
): Artifact | null {
  const t = token.trim();
  if (!t || t.split("/").includes("..")) return null;
  const named = (p: string) => (p.split("/").pop() ?? p).replace(/\.md$/i, "");

  if (/^meeting:transcript$/i.test(t)) {
    return ctx.meetingId ? { kind: "meeting", path: String(ctx.meetingId), label: "Transcript" } : null;
  }
  if (/^meeting:note$/i.test(t)) {
    // THE SERVER SAYS WHERE THE NOTE IS. This used to build `kg/entities/meeting/<native>.md`
    // while `drop_to_attendees` wrote `kg/entities/meeting/<day>-<slug>.md` — one path, two
    // spellings, in two languages, and they never matched: the Minutes tab pointed at a file
    // nothing writes, so it read "No page here yet" on every meeting that HAD been written and
    // mailed. The path now arrives on the scaffold (`refs.note_path`), computed once by the step
    // that writes the file. No path → drop the token, per the rule above.
    if (!ctx.notePath) return null;
    return { path: ctx.notePath, label: ctx.phase === "post" ? "Minutes" : "Brief" };
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
  ctx: {
    native?: string | null; notePath?: string | null; meetingId?: string | null;
    phase?: MeetingPhase | null; mounts?: string[];
  },
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
  // A MEETING, NOT A FILE. `meeting:<row id>` opens the meeting CANVAS — the live transcript that
  // streams segments as they are captured — rather than a document at a path. It reuses the
  // `meeting:` vocabulary the preset tokens already speak, so nothing new goes on the wire.
  //
  // Without this an artifact event could never reach the canvas at all: every event produced a
  // plain doc page, so `openPage` set `docKind: "doc"` and PagesPanel's canvas branch never fired.
  // A harness opening the transcript on bot admission would have rendered a document view of a
  // path that is not a document.
  // The PREFIX claims the ref, not the match: `meeting:` with nothing after it must open NOTHING,
  // not fall through and render a document at the literal path "meeting:".
  if (/^meeting:/.test(path)) {
    const id = path.slice("meeting:".length).trim();
    return id && !id.includes("/") ? { kind: "meeting", path: id, label: "Transcript" } : null;
  }
  // A path that walks out of the mount is refused, as everywhere else here — a write we cannot
  // name honestly opens no tab rather than a tab pointing somewhere it should not.
  if (!path || path.split("/").includes("..")) return null;
  const slug = (ev.workspace ?? "").trim();
  return { path, slug: slug || undefined, label: (path.split("/").pop() ?? path).replace(/\.md$/i, "") };
}

/** AN AGENT'S WRITE NAVIGATES THE VIEW; IT NEVER MINTS A TAB (PRD decision 28).
 *
 *  This replaces the REMOVED `artifactTabEffect`, which appended one. The founder's screenshot: seven tabs after
 *  a few chip clicks, and the same path rendered twice. *"we do not want to create new tab for every
 *  click, tab is only when tab is specifically requested."*
 *
 *  So the rule inverts. The panel has ONE view slot that every navigation REPLACES, and a tab exists
 *  only because somebody asked for one. An `artifact` event is a navigation like any other:
 *
 *    `focus: true`   the turn is saying "look at this" → the view moves.
 *    `focus: false`  the turn wrote a file it is not asking you to read → NOTHING VISIBLE happens.
 *                    Previously this appended a tab "quietly behind the reader", which is exactly
 *                    the accumulation being removed: seven quiet tabs are not quiet.
 *
 *  `readerChoseFocus` still wins over `focus: true`: a reader who has deliberately opened something
 *  during the turn is not interrupted by the agent's write. Their attention beats our suggestion. */
export function artifactViewEffect(
  ev: { workspace?: string; path?: string; focus?: boolean; pin?: boolean },
  readerChoseFocus: boolean,
): { view?: Page; pin?: Page } | null {
  const pg = pageForArtifact(ev);
  if (!pg) return null;
  // TWO INDEPENDENT ASKS. `pin` is about what STAYS in the strip; `focus` is about what is IN
  // FRONT. A turn may want either, both or neither, so they are decided separately rather than one
  // implying the other — a page pinned without focus is the whole point of pinning without
  // interrupting, and a page focused without a pin is an ordinary navigation.
  const view = ev.focus === true && !readerChoseFocus ? pg : undefined;
  const pin = ev.pin === true ? pg : undefined;
  return view || pin ? { view, pin } : null;
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
 *  because a fabricated meeting has nothing for the canvas to fetch.
 *
 *  ⚠ `notePath` IS AN INPUT, and this function cannot know it (Vexa-ai/vexa#1588). The meeting's
 *  own document is `kg/entities/meeting/<meeting-day>-<title-slug>.md`, where the day is rendered
 *  in the ORGANISER's timezone and the slug through a server-side allow-list — neither is derivable
 *  here. This used to build `kg/entities/meeting/<native>.md` and hand it back as Brief/Minutes: a
 *  second spelling of one path, in a second language, and it matched nothing `drop_to_attendees`
 *  writes. The room read "No page here yet" on every meeting whose report HAD been written, mailed
 *  and dropped — which is the same defect `artifactFromToken` fixed for `meeting:note` while this
 *  path kept composing. It arrives from `/api/meeting/note` (or, for a chat born from a link, off
 *  the scaffold), and ABSENT it drops the page rather than guessing one: a tab pointing at a
 *  guessed path opens a document that can never load. */
export function pagesForPhase(phase: MeetingPhase, native?: string | null, meetingId?: string | null,
                              notePath?: string | null): Page[] {
  if (!native) return [personalPage()];
  return [...meetingPages(phase, meetingId, notePath, native), personalPage()];
}

/** THE MEETING'S OWN PAGES — the transcript and its document, in reading order, and nothing else.
 *
 *  Split out of `pagesForPhase` for Vexa-ai/vexa#1597: a chat that BINDS a meeting mid-conversation
 *  gains the meeting's furniture without gaining a room. It already has a home (the strip's desk
 *  entry) and a reader looking at something, so `personalPage()` — the room's "there is always
 *  somewhere to write" — would be a tab nobody asked for. What binding adds is exactly the two
 *  pages that did not exist a moment ago.
 *
 *  ONE WRITER for what a meeting shows: `pagesForPhase` is this plus the personal page, rather than
 *  a second spelling of the same two rules. `native` is here only for the `?mock=1` fallback, where
 *  a fixture with no row keeps its canned markdown transcript. */
export function meetingPages(phase: MeetingPhase, meetingId?: string | null,
                             notePath?: string | null, native?: string | null): Page[] {
  // The meeting doc is the SAME file in every phase — it is the brief while the room is running and
  // the minutes once it is not, so only its NAME moves. That half was always right.
  const doc: Page[] = notePath ? [{ path: notePath, label: phase === "post" ? "Minutes" : "Brief" }] : [];
  // prep: no transcript yet. The one page that matters before the room — what you walk in to decide.
  if (phase === "prep") return doc;
  // live or post: a transcript exists and it leads.
  const transcript: Page = meetingId
    ? { kind: "meeting", path: meetingId, label: "Transcript" }
    : { path: `kg/entities/meeting/${native}.transcript.md`, label: "Transcript" };
  return [transcript, ...doc];
}

/** MERGE the meeting's pages into a strip the reader is already using, PINNED (Vexa-ai/vexa#1597).
 *
 *  The founder's rule for a chat that creates a meeting: *"this transcript should be pinned"*. He
 *  had closed the transcript tab and could not get back to it, because the pages a meeting chat owns
 *  are laid out at OPEN — and this chat was a plain conversation when it opened.
 *
 *  So the binding lays them out now, and it ADDS rather than replaces. The reader is mid-turn with a
 *  document in front of them; re-opening the room would put the desk back in front and undo the
 *  transcript the send itself just fronted. Nothing here decides what is in front — the pages arrive
 *  behind the reader, pinned so the single preview slot cannot evict them, which is exactly the rule
 *  `openChat` applies to a room's own pages (founder ruling #1585: a room's pages are declared tabs).
 *
 *  A page already in the strip is PINNED IN PLACE, never duplicated — and the desk is left alone,
 *  because it is a product default rather than something the reader put there (`homeEntry`). */
export function withMeetingPages(strip: Artifact[], pages: Page[]): Artifact[] {
  const out = [...strip];
  for (const pg of pages) {
    const key = artifactKey(pg);
    const i = out.findIndex((a) => artifactKey(a) === key);
    if (i >= 0) {
      if (!out[i].desk) out[i] = { ...out[i], pinned: true };
      continue;
    }
    out.push({ kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label, pinned: true });
  }
  return orderHistory(out);
}

/** THE RETIRED SPELLING — `kg/entities/meeting/<native>.md`, the path this client used to compose
 *  for a meeting's own document and which nothing has ever written.
 *
 *  It is here to be RECOGNISED, not to be produced: a chat opened before the fix stored it in its
 *  `artifacts[]`, and a stored strip is replayed verbatim on every later open (deliberately — a
 *  reader owns their tabs). So the one tab nobody chose, pointing at a file nobody writes, would
 *  outlive the fix on exactly the desks that hit the bug. `openChat` heals that one entry against
 *  this predicate and touches nothing else — positive evidence about a path we know we minted,
 *  never a tidy-up of somebody's panel. */
export function isRetiredNotePath(path: string, native?: string | null): boolean {
  const n = String(native ?? "").trim();
  return !!n && path === `kg/entities/meeting/${n}.md`;
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
 *  This is THE seam, not a placeholder for one. The click ANNOUNCES a destination and the shell
 *  puts it in front through `openPage` — the single route every other navigation already takes, so
 *  a navigator click, an entity chip, a wikilink and an agent's `artifact` event all land in the
 *  same view slot, the same back/forward stack and the same strip history. The navigator itself
 *  keeps no state about what is being read.
 *
 *  It was written as a stub against a branch that had not landed, and that branch implemented the
 *  slot as an in-shell effect with no event seam — so for a while the placeholder WAS the only
 *  definition, and its listener set the document state directly, bypassing the history and the
 *  strip. A navigator click and a chip click went to two different places. One mechanism now.
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
