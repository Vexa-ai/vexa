/** deepLink — what `/w/<workspace>/<path>` DOES once the URL has been parsed (Vexa-ai/vexa#1643).
 *
 *  **The admin opened `https://app.dev.vexa.ai/w/oenb-b5e60c/README.md` — the README of a shared
 *  workspace he owns — and the terminal started a new chat and showed HIS DESK's README.** Three
 *  separate things had to be true for that, and this module is where the first two are decided:
 *
 *   1. the ref was a SLUG, and the route only recognised ids, so the parse said "not a route";
 *   2. nothing downstream distinguished *"I could not open that"* from *"here is the usual page"*,
 *      so the panel's ordinary default — the desk README — stood in for an answer.
 *
 *  (The third is the server's, and it is the sibling fix on `_read_target`: a workspace this reader
 *  is a member of but has switched OFF is readable by membership and was refused by mount state.)
 *
 *  THE RULE THIS MODULE EXISTS TO KEEP: **a link either opens the page it names, or says one
 *  sentence about why not.** Never the desk instead, and never silence. A person who followed a
 *  link and landed somewhere else cannot tell a spent link from a broken product, and the second
 *  reading is the one they take.
 *
 *  ACCESS IS THE SERVER'S ANSWER. `readable` / `not-yours` / `gone` come back 200 (decision 26.3) —
 *  they are answers, not errors — and every one of them has a rendering here. A lookup that could
 *  not be made at all is a FOURTH state, deliberately kept apart from `gone`: "we could not find
 *  out" is temporary and says so, where `gone` is final.
 *
 *  Pure, except the one lookup, which is injected — so the three kinds of workspace (a desk, a
 *  shared workspace, the company layer) and the refusals are unit-tested with no DOM and no server.
 */
import { isWorkspaceRouteId, type WorkspaceRoute } from "../app/workspaceRoute";
import type { WorkspaceIdentity } from "../surfaces/workspaceApi";
import { isWorkspaceReadme } from "./workspaceReadme";
import type { Chat } from "./chats";
import type { Page } from "./types";

/** The page a workspace ref with no path names: its front page (`WorkspaceReadmePanel` renders the
 *  workspace's own facts above it). The join landing is exactly this — `/w/<id>` after accept. */
export const WORKSPACE_FRONT_PAGE = "README.md";

export type DeepLinkOutcome =
  /** open this page, in this workspace (`workspace` is the SLUG the file API takes). */
  | { kind: "open"; page: Page; workspace?: string }
  /** say this, in the panel, and open nothing. */
  | { kind: "refused"; sentence: string };

/** How the ref is looked up. Two calls because a workspace has two names and only the server can
 *  map one to the other: the immutable id (what a canonical link carries) and the slug it lives
 *  under today (what a person pastes, and what the file API takes). */
export interface WorkspaceLookup {
  byId: (id: string) => Promise<WorkspaceIdentity>;
  bySlug: (slug: string) => Promise<WorkspaceIdentity>;
}

/** What the panel says when the link cannot open. One sentence, no status code, no jargon — the
 *  same discipline `app/join/joinState.refusal` keeps for the invite that cannot be redeemed. */
export function refusalSentence(rec: WorkspaceIdentity | null): string {
  if (!rec) return "That link could not be checked just now — try it again in a moment.";
  if (rec.access === "gone") return "That link points at a workspace that is no longer here.";
  const name = (rec.name || "").trim();
  return name
    ? `That page is in ${name}, a workspace you do not have access to.`
    : "That page is in a workspace you do not have access to.";
}

/** The tab's label. A workspace's README IS its front page (Vexa-ai/vexa#1623), so it wears the
 *  workspace's NAME — never the string "README", and never the slug, which is a directory name
 *  showing through (F49). Any other page is named by its file, as every other tab is. */
export function deepLinkLabel(path: string, rec: WorkspaceIdentity): string {
  if (isWorkspaceReadme(path)) return (rec.name || "").trim() || rec.slug || path;
  return (path.split("/").pop() ?? path).replace(/\.md$/i, "");
}

/** THE DECISION, pure: a parsed route plus the server's answer about the workspace → open, or say.
 *
 *  `rec` is `null` when the lookup itself failed. `slug` is what goes on the page because it is
 *  what `/api/workspace/file` takes; the reader's OWN desk answers with its own slug and the file
 *  route maps that back to their primary, so no case needs special-casing here. */
export function deepLinkOutcome(route: WorkspaceRoute, rec: WorkspaceIdentity | null): DeepLinkOutcome {
  if (!rec || rec.access !== "readable") return { kind: "refused", sentence: refusalSentence(rec) };
  const path = route.path || WORKSPACE_FRONT_PAGE;
  const slug = (rec.slug || "").trim() || undefined;
  return { kind: "open", workspace: slug, page: { path, slug, label: deepLinkLabel(path, rec) } };
}

/** Resolve one parsed route. An id goes to the id resolver and anything else to the slug resolver —
 *  the ref's SHAPE picks the door, and a shape that is both (a ten-character base32 directory name)
 *  is answered by the id, which is the name that cannot go stale. A lookup that throws is not an
 *  error the reader should meet: it becomes the "could not be checked" sentence. */
export async function resolveWorkspaceDeepLink(
  route: WorkspaceRoute, lookup: WorkspaceLookup,
): Promise<DeepLinkOutcome> {
  const rec = await (isWorkspaceRouteId(route.workspace)
    ? lookup.byId(route.workspace)
    : lookup.bySlug(route.workspace)).catch(() => null);
  return deepLinkOutcome(route, rec);
}

/** WHICH CHAT A LINK LANDS IN — *"without starting a new chat when the viewer has chats"*.
 *
 *  A deep link says which PAGE to open; it says nothing about which conversation the reader wants,
 *  so it must never mint one over the top of the conversations they have. In order:
 *
 *    · the chat already AIMED at that workspace (`target`), most recent first — it is the one that
 *      is working there, so a link into it belongs in it;
 *    · else a chat that has that workspace mounted;
 *    · else simply the most recent chat, which is where the terminal opens anyway;
 *    · else null — the caller keeps the draft it is already showing, which is a chat only in the
 *      sense that you can type in it: F35's draft writes no record and leaves nothing behind.
 */
export function chatForDeepLink(chats: Chat[], workspace: string | undefined): Chat | null {
  const byRecent = [...(chats ?? [])].sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0));
  const ws = (workspace || "").trim();
  if (ws) {
    const aimed = byRecent.find((c) => c.target === ws);
    if (aimed) return aimed;
    const mounted = byRecent.find((c) => (c.workspaces ?? []).includes(ws));
    if (mounted) return mounted;
  }
  return byRecent[0] ?? null;
}
