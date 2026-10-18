/** THE DESKTOP AS DRAWN (#1783, desktop ADR §2.1 "the desktop as drawn: nobody — a read model").
 *
 *  The shapes the two owners answer and the ONE pure function that joins them:
 *   - crm-api `desk/read` → `Desk`: the person's arrangement (layout, placements, pending proposals,
 *     saved names, the version Undo goes back to);
 *   - agent-api `desktop/sections` → `DesktopSections`: the workspace tiles and the placed agents and
 *     files, resolved under the viewer's access;
 *   - `composeDesktop(desk, sections)` → `Desktop`: sections in the person's order, each with its
 *     tiles in the person's order. Nothing is stored; the `/api/desktop` route composes per request.
 *
 *  Rules (ADR §2.3a): the default order is Workspaces, Agents, Files, Cards; a section type the
 *  layout does not mention yet is appended at the END, never above what the person arranged; a
 *  placement agent-api could not resolve is drawn as `missing`.
 *
 *  CARDS (D2, feature `desktop_cards`): crm-api's desk read carries `cards` (the cards the person is a
 *  member of or placed, minus the ones they took off), `card_proposals` and `card_invites` ONLY while
 *  the feature is on. Without the key the Cards section stays hidden and the composition carries
 *  nothing card-related (`cards_on: false`), so the page makes no card call.
 *  Shared by the server route and the page, so it imports nothing. */

export const DESKTOP_SECTIONS = ["workspaces", "agents", "files", "cards"] as const;
export type DesktopSectionId = (typeof DESKTOP_SECTIONS)[number];
export const SECTION_TITLES: Record<DesktopSectionId, string> = { workspaces: "Workspaces", agents: "Agents", files: "Files", cards: "Cards" };
/** Sections that exist in the layout but are drawn only while the desk read carries them. */
export const HIDDEN_IN_D1: readonly string[] = ["cards"];

// ── crm-api: cards (D2) ───────────────────────────────────────────────────────────────────────
export type CardAbout = { kind: "page" | "chat" | "workspace" | "meeting" | "crm" | string; ref: string };
export type CardMovement = { by: string; kind: "person" | "agent"; what: string; at: string | number };
export type CardChild = { id: string; title: string; state: string; done: boolean };
export type CardMember = { subject: string; status: "invited" | "member"; name?: string; initials?: string };
/** A resolved face block as crm-api answers it for this viewer (the client sanitizes again). */
export type ResolvedFaceBlock = { type: string } & Record<string, unknown>;
export type CardActivity = { by: string; kind: string; what: string; at: string | number };
export type CardView = {
  id: string; ref: string; type: string; revision: number; title: string; about: CardAbout | null;
  state: string; states: string[]; side_states: string[]; next: string | null; waiting: string | null;
  summary: string | null; due: string | null; progress: number | null; workspaces: string[];
  parent: { id: string; title: string } | null;
  children: { done: number; total: number; items: CardChild[] };
  members: CardMember[]; face: ResolvedFaceBlock[]; movement: CardMovement | null; stalled: boolean;
  can: { edit: boolean; transition: boolean; review: boolean; members: boolean }; href: string;
  /** `/card/read` only */
  activity?: CardActivity[];
  /** placed or joined since the person last opened it */
  new?: boolean;
};
export type CardProposal = {
  id: string; kind: "create" | "change"; card?: { id: string; title: string } | null; title: string;
  workspace: string | null; fields: Record<string, unknown>; reason: string; author_kind: "agent" | "person" | string;
  author?: string | null; at: string | number; preview?: Partial<CardView> | null;
};

// ── crm-api: the person's desk ────────────────────────────────────────────────────────────────
export type DeskSectionLayout = { id: string; hidden?: boolean; collapsed?: boolean; column?: number; span?: number; size?: string };
export type DeskFilter = { where: string; until?: string | null };
export type DeskLayout = {
  sections?: DeskSectionLayout[];
  order?: Record<string, string[]>;
  groups?: Record<string, unknown>;
  filters?: Record<string, DeskFilter | null>;
  columns?: { phone?: number; desktop?: number };
};
export type DeskPlacement = { ref: string; placed_at?: string | number | null; seen_at?: string | number | null };
export type DeskProposal = { id: string; ops: unknown[]; reason?: string; author?: string; created_at?: string | number; summary?: string };
export type Desk = {
  version: number; layout: DeskLayout; placements: DeskPlacement[]; proposals: DeskProposal[];
  saved: string[]; undo_version: number | null;
  /** present only while `desktop_cards` is on */
  cards?: CardView[]; card_proposals?: CardProposal[]; card_invites?: CardView[];
};

// ── agent-api: the tiles ──────────────────────────────────────────────────────────────────────
export type Presence = "now" | "today" | null;
/** meeting-api's platform names for a call: a bot meeting, or an in-app Vexa call. */
export type CallPlatform = "google_meet" | "teams" | "zoom" | "jitsi" | "vexa_call";
export type WorkspaceMember = { subject: string; name: string; initials: string; role: string; presence?: Presence };
export type WorkspaceInfo = {
  slug: string; id: string; name: string; kind: "private" | "shared" | "company"; access: string; write: boolean;
  members: WorkspaceMember[];
  last_activity?: { line: string; at: number } | null;
  /** The newest ended meeting that belongs here, of any kind (bot meeting or Vexa call). */
  last_call?: { meeting: string; platform?: CallPlatform; line: string; at: number } | null;
  /** A meeting happening now: `session` only for a Vexa call (Join opens its chat); a bot meeting
   *  opens itself. `people` when known. */
  live_call?: { session?: string; meeting?: string; platform?: CallPlatform; line?: string; people?: number; since: number } | null;
};
export type AgentInfo = {
  ref: string; id: string; name: string; what?: string; status?: "running" | "idle" | "paused" | "failed";
  last?: string; next?: string | null; where?: string | null; thread?: string | null; missing: boolean;
  /** flows did not answer: the tile says so rather than guessing a status */
  unavailable?: boolean;
};
export type FileInfo = { ref: string; slug: string; path: string; title: string; where?: string | null; changed?: string; missing: boolean };
/** One of the person's own chats placed on the desktop (`chat:<session id>`); drawn in Agents. */
export type ChatInfo = { ref: string; id: string; title: string; last?: string | null; last_at?: number | null; where?: string | null; missing: boolean };
export type DesktopSections = {
  presence_visibility: string; workspaces: WorkspaceInfo[]; hidden_workspaces: number;
  agents: AgentInfo[]; files: FileInfo[];
  /** present only when `chat:` refs were asked for */
  chats?: ChatInfo[];
};

// ── the composed desktop ──────────────────────────────────────────────────────────────────────
export type WorkspaceItem = { kind: "workspace"; ref: string; workspace_kind: WorkspaceInfo["kind"] } & Omit<WorkspaceInfo, "kind">;
export type AgentItem = { kind: "agent" } & AgentInfo;
export type FileItem = { kind: "file" } & FileInfo;
export type ChatItem = { kind: "chat" } & ChatInfo;
export type CardItem = { kind: "card"; missing?: false } & CardView;
export type DesktopItem = WorkspaceItem | AgentItem | ChatItem | FileItem | CardItem;
export type DesktopSection = {
  id: string; title: string; hidden: boolean; collapsed: boolean; column: number; span: number; size: string; items: DesktopItem[];
};
export type Desktop = {
  version: number; undo_version: number | null; sections: DesktopSection[]; proposals: DeskProposal[];
  filters: { section: string; where: string; until?: string | null }[]; saved: string[];
  hidden_workspaces: number; presence_visibility: string;
  /** the desk read carried cards (`desktop_cards` on); false → nothing card-related is drawn */
  cards_on: boolean;
  card_proposals?: CardProposal[]; card_invites?: CardView[];
};

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const KNOWN = new Set<string>(DESKTOP_SECTIONS);

/** The refs agent-api resolves into tiles: the person's agent, chat and file placements, once each. */
export function placedRefs(desk: Pick<Desk, "placements"> | null | undefined): string[] {
  const out: string[] = [];
  for (const p of arr<DeskPlacement>(desk?.placements)) {
    const ref = typeof p?.ref === "string" ? p.ref : "";
    if ((ref.startsWith("agent:") || ref.startsWith("chat:") || ref.startsWith("file:")) && !out.includes(ref)) out.push(ref);
  }
  return out;
}

/** Items first in the layout's order (ids that no longer exist are skipped), the rest after in
 *  their natural order. */
function ordered<T extends { ref: string }>(items: T[], order: unknown): T[] {
  const ids = arr<string>(order);
  const by = new Map(items.map((i) => [i.ref, i]));
  const first = ids.filter((id, i) => by.has(id) && ids.indexOf(id) === i).map((id) => by.get(id)!);
  const seen = new Set(first.map((i) => i.ref));
  return [...first, ...items.filter((i) => !seen.has(i.ref))];
}

function missingFile(ref: string): FileItem {
  const rest = ref.slice("file:".length);
  const cut = rest.indexOf("/");
  const slug = cut > 0 ? rest.slice(0, cut) : rest;
  const path = cut > 0 ? rest.slice(cut + 1) : "";
  const title = (path.split("/").pop() || path || ref).replace(/\.md$/i, "");
  return { kind: "file", ref, slug, path, title, missing: true };
}

const isCard = (c: unknown): c is CardView => !!c && typeof c === "object" && typeof (c as CardView).id === "string" && !!(c as CardView).id;
const cardItem = (c: CardView): CardItem => ({ ...c, kind: "card" as const, ref: typeof c.ref === "string" && c.ref ? c.ref : `card:${c.id}` });

export function composeDesktop(desk: Desk, sections: DesktopSections, now: number = Date.now()): Desktop {
  const layout: DeskLayout = (desk && typeof desk.layout === "object" && desk.layout) || {};
  const order = (layout.order && typeof layout.order === "object" ? layout.order : {}) as Record<string, unknown>;

  const workspaces: WorkspaceItem[] = arr<WorkspaceInfo>(sections?.workspaces).filter((w) => w && typeof w.slug === "string")
    .map(({ kind, ...w }) => ({ ...w, members: arr<WorkspaceMember>(w.members), kind: "workspace" as const, ref: `workspace:${w.slug}`, workspace_kind: kind }));
  const agentsBy = new Map(arr<AgentInfo>(sections?.agents).filter((a) => a && a.ref).map((a) => [a.ref, a]));
  const filesBy = new Map(arr<FileInfo>(sections?.files).filter((f) => f && f.ref).map((f) => [f.ref, f]));
  const refs = placedRefs(desk);
  const chatsBy = new Map(arr<ChatInfo>(sections?.chats).filter((c) => c && c.ref).map((c) => [c.ref, c]));
  // Agents holds the routines and the chats, in placement order (a chat is an agent you talk to)
  const agents: (AgentItem | ChatItem)[] = refs.filter((r) => r.startsWith("agent:") || r.startsWith("chat:")).map((ref) => {
    if (ref.startsWith("chat:")) {
      const c = chatsBy.get(ref);
      return c ? { ...c, kind: "chat" as const, missing: !!c.missing } : { kind: "chat" as const, ref, id: ref.slice("chat:".length), title: "", missing: true };
    }
    const a = agentsBy.get(ref);
    return a ? { ...a, kind: "agent" as const, missing: !!a.missing } : { kind: "agent" as const, ref, id: ref.slice("agent:".length), name: "", missing: true };
  });
  const files: FileItem[] = refs.filter((r) => r.startsWith("file:")).map((ref) => {
    const f = filesBy.get(ref);
    return f ? { ...f, kind: "file" as const, missing: !!f.missing } : missingFile(ref);
  });
  const cardsOn = Array.isArray(desk?.cards);
  const items: Record<DesktopSectionId, DesktopItem[]> = {
    workspaces: ordered(workspaces, order.workspaces),
    agents: ordered(agents, order.agents),
    files: ordered(files, order.files),
    cards: cardsOn ? ordered(arr<unknown>(desk.cards).filter(isCard).map(cardItem), order.cards) : [],
  };

  const laid = arr<DeskSectionLayout>(layout.sections).filter((s, i, all) => s && KNOWN.has(s.id) && all.findIndex((x) => x?.id === s.id) === i);
  const appended = DESKTOP_SECTIONS.filter((id) => !laid.some((s) => s.id === id)).map((id) => ({ id }) as DeskSectionLayout);
  const drawn: DesktopSection[] = [...laid, ...appended].map((s) => {
    const id = s.id as DesktopSectionId;
    return {
      id, title: SECTION_TITLES[id],
      hidden: (id === "cards" && !cardsOn) || !!s.hidden,
      collapsed: !!s.collapsed,
      column: Number.isInteger(s.column) ? (s.column as number) : 1,
      span: Number.isInteger(s.span) ? (s.span as number) : 1,
      size: typeof s.size === "string" ? s.size : "normal",
      items: items[id],
    };
  });

  const filters: Desktop["filters"] = [];
  for (const [section, f] of Object.entries(layout.filters && typeof layout.filters === "object" ? layout.filters : {})) {
    if (!f || typeof f.where !== "string" || !f.where) continue;
    if (f.until) { const t = Date.parse(f.until); if (!Number.isNaN(t) && t <= now) continue; }
    filters.push(f.until ? { section, where: f.where, until: f.until } : { section, where: f.where });
  }

  return {
    version: Number.isInteger(desk?.version) ? desk.version : 0,
    undo_version: Number.isInteger(desk?.undo_version) ? (desk.undo_version as number) : null,
    sections: drawn,
    proposals: arr<DeskProposal>(desk?.proposals).filter((p) => p && typeof p.id === "string"),
    filters,
    saved: arr<string>(desk?.saved).filter((n) => typeof n === "string"),
    hidden_workspaces: Number(sections?.hidden_workspaces) || 0,
    presence_visibility: typeof sections?.presence_visibility === "string" ? sections.presence_visibility : "off",
    cards_on: cardsOn,
    ...(cardsOn ? {
      card_proposals: arr<CardProposal>(desk.card_proposals).filter((p) => p && typeof p.id === "string" && !!p.id),
      card_invites: arr<unknown>(desk.card_invites).filter(isCard).map((c) => ({ ...c, ref: c.ref || `card:${c.id}` })),
    } : {}),
  };
}
