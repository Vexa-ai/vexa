/** WHAT A WORKSPACE'S FRONT PAGE KNOWS — the facts behind `WorkspaceReadmePanel`, as pure functions
 *  plus one loader (Vexa-ai/vexa#1623).
 *
 *  Founder, 2026-09-06, on the OeNB workspace's `README.md` open in the preview: *"ok click we want
 *  to open the workspace readme — if it's a workspace readme we want to have data: shared with whom,
 *  controls like github sync, git history lookup, etc."* A workspace's README is not a page that
 *  happens to be called README: it is the workspace's front page, and the workspace's own facts
 *  belong on it, above the prose, distinct from the page chrome.
 *
 *  EVERY FACT IS READ, NONE IS RETYPED. That is the rule this module is built around, and it is why
 *  the policy sentence is PARSED out of `_global/POLICIES.md` rather than written here as a nice
 *  string. A sentence retyped into a client is a sentence that keeps saying what the rules used to
 *  be: the admin changes `global_admin_only` and the panel goes on reassuring everybody about the
 *  old answer. Parsing costs a file read and a regex; being wrong about who may read a bank's
 *  workspace costs more than that.
 *
 *  WHAT IT REFUSES TO GUESS. Every field is nullable and every failed read lands in `notes` as a
 *  sentence naming what could not be read. A panel that renders "0 pages" when the tree read failed,
 *  or "no members" when the roster 403'd, has told the reader something false about their own
 *  workspace — and this is the one page whose whole job is to be true about that.
 */
import { presentError } from "../surfaces/apiClient";
import {
  gitRemoteStatus, listSharedMemberships, listWorkspaceMembers, listWorkspaceTree,
  readWorkspaceBySlug, readWorkspaceFile, readWorkspaceHistory,
  type GitCommit, type GitRemoteStatus, type WorkspaceMember,
} from "../surfaces/workspaceApi";
import { isMachinery } from "./machinery";

/** The server's own three (`workspaces/shared/workspace_id.KINDS`). A "customer workspace" is a
 *  group whose members happen to include a customer — a fact about the people, not a fourth kind. */
export type WorkspaceKind = "desk" | "group" | "global";

/** The company layer's mount slug, and the desk's name in a path segment (the terminal's desk tab
 *  carries no slug at all, so `GET /api/workspaces/{slug}/history` needs a word for it — the same
 *  word `workspace_attach.PERSONAL_ALIAS` and the breadcrumb already use). */
export const GLOBAL_SLUG = "_global";
export const DESK_SLUG = "personal";

/** IS THIS A WORKSPACE'S FRONT PAGE? A README at the workspace ROOT, and nothing else: a
 *  `docs/README.md` is a page about docs, and putting the workspace's membership above it would be
 *  a claim about the wrong thing. Case-insensitive because git is not, and the file is `README.md`
 *  on every workspace we seed but `Readme.md` in plenty of repositories people attach. */
export const isWorkspaceReadme = (path: string): boolean =>
  /^readme\.mdx?$/i.test(String(path ?? "").trim());

/** What a person calls each kind. The server's word is kept beside it in the panel — this is the
 *  gloss, not a replacement, because `group` is what every API answer says. */
export const kindLabel = (kind: WorkspaceKind): string =>
  kind === "desk" ? "Personal desk" : kind === "global" ? "Company layer" : "Shared workspace";

/** Markdown markers out of a sentence lifted from `POLICIES.md`, so it reads as a sentence in a
 *  panel that is not rendering markdown. Deliberately only the three that appear there. */
const plain = (s: string): string =>
  s.replace(/\*\*/g, "").replace(/`/g, "").replace(/\s+/g, " ").trim().replace(/[;.]$/, "");

/** `### \`<key>\` — <the sentence>` — how `POLICIES.md` states each rule in its own heading. */
function ruleHeading(md: string, key: string): string | null {
  const m = new RegExp(`^###\\s+\`${key}\`\\s*[—-]\\s*(.+)$`, "m").exec(md);
  return m ? plain(m[1]) : null;
}

/** A bullet from `## What is not yours to choose`, joined across its wrapped continuation lines. */
function bullet(md: string, needle: string): string | null {
  const lines = md.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (!/^[-*]\s+/.test(lines[i])) continue;
    let text = lines[i].replace(/^[-*]\s+/, "");
    for (let j = i + 1; j < lines.length && /^\s{2,}\S/.test(lines[j]); j++) text += ` ${lines[j].trim()}`;
    if (text.toLowerCase().includes(needle.toLowerCase())) return plain(text);
  }
  return null;
}

/** WHO MAY READ THIS WORKSPACE AND WHO WRITES IT — in `_global/POLICIES.md`'s own words.
 *
 *  One kind, one sentence, and each is the sentence that file already carries about that kind:
 *  a group is the *"a member reads a group; an owner or contributor writes it"* bullet under
 *  *What is not yours to choose*; a desk is the `agent_reads_desk` rule; the company layer is
 *  `global_admin_only`. `null` when the file could not be read or has been rewritten past
 *  recognition — the panel then says so, rather than inventing a rule. */
export function policySentence(kind: WorkspaceKind, policies: string | null): string | null {
  if (!policies) return null;
  if (kind === "group") return bullet(policies, "reads a group");
  if (kind === "desk") return ruleHeading(policies, "agent_reads_desk");
  return ruleHeading(policies, "global_admin_only");
}

/** WHICH PAGES A PERSON HAS HERE. `.md` only, and only the ones a reader is shown: the same
 *  `isMachinery` list the navigator and the breadcrumb use, so the count and the tree cannot
 *  disagree about what is in this workspace. The LIST and the COUNT come from this one filter
 *  because the strip's `N pages` summary and the section it opens are the same claim — a count that
 *  disagrees with the list under it is the panel lying to itself in two lines. */
export const pagePaths = (paths: readonly string[]): string[] =>
  paths.filter((p) => /\.mdx?$/i.test(p) && !isMachinery(p));
export const countPages = (paths: readonly string[]): number => pagePaths(paths).length;

/** A meeting row, as the meetings list serves it — the fields a binding is made of, and no others. */
export interface MeetingRow {
  id?: number | string;
  status?: string;
  start_time?: string | null;
  data?: {
    workspace_id?: string | null;
    calendar_uid?: string | null;
    title?: string | null;
    scheduled_at?: string | null;
  } | null;
}

/** One thing this workspace is bound to: a recurring invite (every run of it shares a
 *  `calendar_uid`) or a single meeting that dropped into it. */
export interface BoundSeries {
  key: string;
  title: string;
  recurring: boolean;
  runs: number;
  /** the most recent run's time — when it ran, else when it is due, else "" */
  latest: string;
}

/** WHAT DROPPED INTO THIS WORKSPACE. meeting-api owns the binding (`data.workspace_id`, set by
 *  `POST /meetings/{platform}/{native}/workspace` and read server-side by
 *  `meeting_room.group_workspace_id`), so the answer is a filter over the caller's own meetings —
 *  never a second store. Runs of one recurring invite collapse into one row: three occurrences of
 *  the Tuesday sync is one thing this workspace is bound to, not three. */
export function boundSeries(rows: readonly MeetingRow[], slug: string): BoundSeries[] {
  const byKey = new Map<string, BoundSeries>();
  for (const r of rows) {
    const d = r?.data ?? {};
    if (!slug || String(d.workspace_id ?? "") !== slug) continue;
    const uid = String(d.calendar_uid ?? "").trim();
    const key = uid ? `cal:${uid}` : `row:${r.id ?? ""}`;
    const when = String(r.start_time ?? d.scheduled_at ?? "");
    const found = byKey.get(key);
    if (found) {
      found.runs += 1;
      if (when > found.latest) found.latest = when;
    } else {
      byKey.set(key, {
        key, title: String(d.title ?? "").trim() || "Untitled meeting",
        recurring: !!uid, runs: 1, latest: when,
      });
    }
  }
  return [...byKey.values()].sort((a, b) => b.latest.localeCompare(a.latest));
}

/** A member's role, as the roster and the membership list both spell it. `viewer` is what the API
 *  says; *reader* is what the panel shows a person, per the issue's own vocabulary. */
export type Role = "owner" | "contributor" | "viewer";
export const roleLabel = (role: string): string =>
  role === "viewer" ? "reader" : role === "owner" ? "owner" : role === "contributor" ? "contributor" : role;

export interface WorkspaceFacts {
  /** the address this panel's reads use — `personal`, `_global`, or the workspace id */
  slug: string;
  kind: WorkspaceKind;
  name: string | null;
  pages: number | null;
  /** the pages themselves — the same filter the count comes from, `null` when the tree read failed */
  pageList: string[] | null;
  lastChange: GitCommit | null;
  policy: string | null;
  bound: BoundSeries[];
  /** this reader's own role in a shared workspace (`null` for a desk / the company layer) */
  myRole: Role | null;
  /** the roster — `null` when this reader may not read it (a reader of a group may not) */
  members: WorkspaceMember[] | null;
  remote: GitRemoteStatus | null;
  /** WHY the GitHub read failed, in the presenter's words — `null` when it did not fail.
   *
   *  It is a field of its own, and not a line in `notes`, because *no repo attached* and *the read
   *  failed* are different facts and the panel was rendering them as one: on 2026-09-06 `_global`
   *  showed `not readable` with `Could not read the GitHub state.` in red under it, for the
   *  administrator, on a workspace that simply has no remote (Vexa-ai/vexa#1628). A red line that
   *  fires on an ordinary state is a red line nobody reads on the day it means something. */
  remoteFailure: string | null;
  /** may this reader operate the owner-only controls? The server decides again on every act. */
  owner: boolean;
  /** what could not be read, in sentences — never silently rendered as an empty or zero fact */
  notes: string[];
}

/** SHARED WITH, IN FIVE WORDS — the strip's summary of the roster, which opens the section that
 *  carries the whole of it (Vexa-ai/vexa#1628). Five words is the budget the founder set for the
 *  collapsed line, so the sentence has to be the ANSWER rather than a label for one. */
export function sharedInFiveWords(f: Pick<WorkspaceFacts, "kind" | "members" | "myRole">): string {
  if (f.kind === "desk") return "Yours; company agents read it";
  if (f.kind === "global") return "Everyone reads, the admin writes";
  if (f.members) {
    const n = f.members.length;
    return `${n} member${n === 1 ? "" : "s"}${f.myRole ? `, you ${roleLabel(f.myRole)}` : ""}`;
  }
  return f.myRole ? `You are a ${roleLabel(f.myRole)}` : "A shared workspace";
}

/** THE REPO STATE IN THREE WORDS. The distinction the whole of #1628's third point is about lives
 *  here: `no repo attached` is a state, `could not read` is a failure, and they are never the same
 *  three words. */
export function repoInThreeWords(remote: GitRemoteStatus | null, failure: string | null): string {
  if (failure) return "could not read";
  if (!remote) return "reading the repo";
  if (!remote.has_home) return "no repo attached";
  const branch = remote.branch ?? "HEAD";
  return remote.tracked ? `${branch}, ${remote.ahead} ahead` : `${branch}, never fetched`;
}

/** THE LAST CHANGE, in the strip's one line: what was done, by whom, when. */
export function lastChangeLine(c: GitCommit | null): string {
  if (!c) return "nothing committed yet";
  const msg = c.msg.length > 44 ? `${c.msg.slice(0, 43)}…` : c.msg;
  return `${msg} · ${c.author ?? "unknown"} · ${c.when}`;
}

/** Is the signed-in person this instance's admin? Only a literal `true` counts, and an unanswered
 *  probe costs an OFFER, never a refusal — the write seam refuses a non-admin either way. */
async function amAdmin(): Promise<boolean> {
  try {
    const r = await fetch("/api/auth/me", { cache: "no-store" });
    if (!r.ok) return false;
    return ((await r.json()) as { is_admin?: boolean } | null)?.is_admin === true;
  } catch { return false; }
}

async function meetingRows(): Promise<MeetingRow[]> {
  const r = await fetch("/api/meetings", { cache: "no-store" });
  if (!r.ok) throw new Error(`meetings ${r.status}`);
  const body = (await r.json()) as MeetingRow[] | { meetings?: MeetingRow[] };
  return Array.isArray(body) ? body : body?.meetings ?? [];
}

/** EVERYTHING THE FRONT PAGE SHOWS, in one call, from the routes that already exist.
 *
 *  `docSlug` is the panel's own address for the open document: absent means the reader's own desk
 *  (the desk tab carries no slug, and every other tab does), `_global` is the company layer, and
 *  anything else is a workspace id.
 *
 *  `allSettled`, deliberately: these are five to eight independent reads and one of them failing is
 *  a missing SECTION, not a missing panel. A roster the reader may not see is the ordinary case for
 *  a reader, not an error — so a 403 there leaves `members: null` and no note at all. */
export async function loadWorkspaceFacts(docSlug: string | undefined): Promise<WorkspaceFacts> {
  const isDesk = !docSlug;
  const isGlobal = docSlug === GLOBAL_SLUG;
  const slug = isDesk ? DESK_SLUG : (docSlug as string);
  const notes: string[] = [];
  const note = (what: string) => { notes.push(what); };

  // The identity read is what says which KIND this is — and for anything but the desk and the
  // company layer it is the only thing that does.
  const [ident, tree, history, remote, policies] = await Promise.allSettled([
    isDesk || isGlobal ? Promise.resolve(null) : readWorkspaceBySlug(slug),
    listWorkspaceTree({ slug: isDesk ? undefined : slug }),
    // ONE commit, because one is all this read is for: the strip's *last change*. The list the
    // history disclosure shows is `loadHistory`'s own paged read, and asking for twenty here bought
    // nineteen commits nothing rendered.
    readWorkspaceHistory(slug, { limit: 1 }),
    gitRemoteStatus({ slug: isDesk ? undefined : slug }),
    readWorkspaceFile("POLICIES.md", { slug: GLOBAL_SLUG }),
  ]);

  const identity = ident.status === "fulfilled" ? ident.value : null;
  const kind: WorkspaceKind = isDesk ? "desk" : isGlobal ? "global"
    : identity?.kind === "desk" ? "desk" : identity?.kind === "global" ? "global" : "group";
  if (ident.status === "rejected") note("Could not read what this workspace is.");
  if (tree.status === "rejected") note("Could not count the pages.");
  if (history.status === "rejected") note("Could not read the history.");
  if (policies.status === "rejected") note("Could not read the company policy.");
  // The GitHub read's failure is NOT a note: it belongs to its own section, named, so that the red
  // line at the foot keeps meaning "something here is broken" (#1628 point 3).
  const remoteFailure = remote.status === "rejected" ? presentError(remote.reason).headline : null;

  const commits = history.status === "fulfilled" ? history.value.commits : [];
  const policy = policySentence(kind, policies.status === "fulfilled" ? policies.value : null);
  if (policies.status === "fulfilled" && policies.value && !policy) {
    note("The company policy does not state a rule for this kind of workspace.");
  }

  // A group's people, its bindings, and this reader's own standing. None of it applies to a desk
  // (one owner, no roster) or to the company layer (everybody reads, the admin writes), so neither
  // pays for the round trips.
  let myRole: Role | null = null;
  let members: WorkspaceMember[] | null = null;
  let bound: BoundSeries[] = [];
  if (kind === "group") {
    const [mine, roster, meetings] = await Promise.allSettled([
      listSharedMemberships(), listWorkspaceMembers(slug), meetingRows(),
    ]);
    if (mine.status === "fulfilled") {
      const row = mine.value.find((m) => m.workspace_id === slug);
      myRole = (row?.role as Role) ?? null;
    } else note("Could not read your own role here.");
    // A READER MAY NOT READ THE ROSTER, and that is the design rather than a failure — the members
    // route is contributor+. So a rejection here is silent, and the section says what it does know.
    if (roster.status === "fulfilled") members = roster.value;
    if (meetings.status === "fulfilled") bound = boundSeries(meetings.value, slug);
    else note("Could not read what this workspace is bound to.");
  }

  const owner = kind === "global" ? await amAdmin()
    : kind === "desk" ? (isDesk || identity?.writable === true)
    : myRole === "owner";

  const pageList = tree.status === "fulfilled" ? pagePaths(tree.value) : null;
  return {
    slug, kind, name: identity?.name ?? null,
    pages: pageList ? pageList.length : null,
    pageList,
    lastChange: commits[0] ?? null,
    policy, bound, myRole, members,
    remote: remote.status === "fulfilled" ? remote.value : null,
    remoteFailure,
    owner, notes,
  };
}

/** HOW MANY COMMITS THE HISTORY SHOWS BEFORE *more* — ten (Vexa-ai/vexa#1628 point 4), the same in
 *  the whole-workspace view and in the one-page view. */
export const HISTORY_PAGE = 10;

/** The history list is loaded separately from the facts above: it is re-read whenever the page
 *  filter is toggled, and re-reading a workspace's whole identity to change one query parameter
 *  would be four round trips to answer a question about one.
 *
 *  It asks for ONE MORE than it will show, which is how *more* can exist without a second question:
 *  the eleventh commit is never rendered, it is the answer to "is there an eleventh". */
export async function loadHistory(slug: string, path: string | undefined, shown = HISTORY_PAGE): Promise<GitCommit[]> {
  return (await readWorkspaceHistory(slug, { path, limit: shown + 1 })).commits;
}
