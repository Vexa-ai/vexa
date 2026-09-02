"use client";
/** THE SCAFFOLD — one record per arrival (PRD §5.5).
 *
 *  A scaffold says, for one moment a person arrives at, what the agent knows and what the UI
 *  shows. The flow mints it when it creates a touch; the link carries only its **id** (plus the
 *  share); the terminal and the agent both render from it. Nothing on either side is composed from
 *  whatever happened to be there before — which is the whole point, because "composed from
 *  whatever was there" is the shape of every seam failure the founder hit.
 *
 *  THIS FILE IS THE ONLY PLACE THAT KNOWS THE WIRE SHAPE. The server half lands on its own branch
 *  and posts its response shape; everything else in the client consumes `Scaffold`, so reconciling
 *  with the real interface is an edit to `parseScaffold` and nothing else. That is deliberate: two
 *  workers building two halves of one contract the same afternoon is exactly how the
 *  `room_read` / `room_participants` mismatch happened, and it 422'd every dispatch.
 *
 *  Two rules the parse enforces, both from §5.5:
 *    · **phase is resolved at OPEN, never at mint** — so it is read from the response, never
 *      remembered from the link. A "prep" link clicked after the meeting must not lie.
 *    · **the opening is a preset NAME plus text the SERVER substituted** — the URL never carries
 *      prompt text, because a link that could would let anyone who can send mail drive the
 *      recipient's agent.
 */
import type { MeetingPhase } from "../surfaces/meetingModel";
import { artifactKey, meetingChatId, type Artifact } from "./chats";
import { artifactsFromTokens } from "./roomView";

/** What a scaffold says about the person and the room, for a preset to branch on. */
export interface ScaffoldRefs {
  title?: string;
  when?: string;
  organizer?: string;
  participants: string[];
  participantNames: Record<string, string>;
  /** coarse, deliberately: `desk` new|pile|warm · `group` absent|new|warm */
  state: { desk?: string; group?: string };
}

export interface Scaffold {
  id: string;
  kind: string;
  /** the meetings-domain ROW id, or null for a scaffold that is not about a meeting */
  meeting: string | null;
  /** the meeting's NATIVE id, read off the row by the server.
   *
   *  Two identifiers, deliberately both present: the canvas binds to the ROW id, and
   *  `kg/entities/meeting/<native>.md` is keyed by the native. Carrying it on the record is what
   *  lets the client stop hunting the meetings list for it — and the list is exactly the thing that
   *  may not have loaded yet when an emailed link lands. */
  native: string | null;
  /** resolved SERVER-SIDE at open — never at mint, never inferred here */
  phase: MeetingPhase | null;
  workspaces: string[];
  refs: ScaffoldRefs;
  openingPreset: string;
  /** already substituted server-side, and machinery: the human never sees it as their own words */
  openingText: string;
  tabs: string[];
  focus: string;
  provenance: string;
  redeemedAt: string | null;
}

/** Why a scaffold could not be opened. Never a blank chat: a person who clicked a real link and
 *  landed on nothing cannot tell a revoked invitation from a broken product. */
export interface ScaffoldRefusal {
  reason: "not-found" | "forbidden" | "unavailable" | "malformed";
  status: number;
  detail: string;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const strArr = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && !!x.trim()).map((x) => x.trim()) : [];

const PHASES: MeetingPhase[] = ["prep", "live", "post"];

/** The wire → `Scaffold`, or `null` when the body is not one.
 *
 *  Tolerant on every optional field and STRICT on exactly two — `id` and `opening_text` — because a
 *  scaffold without them cannot open a chat, and rendering an empty conversation is worse than
 *  saying plainly that the link did not resolve. */
export function parseScaffold(raw: unknown): Scaffold | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id).trim();
  const openingText = str(r.opening_text);
  if (!id || !openingText.trim()) return null;

  const refsRaw = (r.refs && typeof r.refs === "object" ? r.refs : {}) as Record<string, unknown>;
  const stateRaw = (refsRaw.state && typeof refsRaw.state === "object" ? refsRaw.state : {}) as Record<string, unknown>;
  const namesRaw = (refsRaw.participant_names && typeof refsRaw.participant_names === "object"
    ? refsRaw.participant_names : {}) as Record<string, unknown>;
  const names: Record<string, string> = {};
  for (const [k, v] of Object.entries(namesRaw)) if (typeof v === "string") names[k] = v;

  const phaseRaw = str(r.phase).trim().toLowerCase() as MeetingPhase;
  const meeting = r.meeting == null || r.meeting === "" ? null : String(r.meeting);
  const native = str(r.native).trim() || null;

  return {
    id,
    kind: str(r.kind) || "unknown",
    meeting,
    native,
    phase: PHASES.includes(phaseRaw) ? phaseRaw : null,
    // `_global` is always mounted by the server's own rule; we do not add it here, because a
    // client that patches the mount set is a second opinion about context.
    workspaces: strArr(r.workspaces),
    refs: {
      title: str(refsRaw.title) || undefined,
      when: str(refsRaw.when) || undefined,
      organizer: str(refsRaw.organizer) || undefined,
      participants: strArr(refsRaw.participants),
      participantNames: names,
      state: { desk: str(stateRaw.desk) || undefined, group: str(stateRaw.group) || undefined },
    },
    openingPreset: str(r.opening_preset),
    openingText,
    tabs: strArr(r.tabs),
    focus: str(r.focus),
    // `provenance` on the wire is the OBJECT {flow, step, reaction_id, minted_by}; the string is
    // `provenance_line`. Reading the object through `str()` degraded to "" silently — four facts
    // cannot be a string, and it is the line we want to show.
    provenance: str(r.provenance_line),
    redeemedAt: str(r.redeemed_at) || null,
  };
}

/** Fetch one scaffold AS THE SIGNED-IN IDENTITY. The server decides whether it is theirs; the
 *  client never asserts who it is for. A refusal is returned, never thrown — the caller renders a
 *  card that states the situation. */
export async function fetchScaffold(
  id: string,
  fetcher: typeof fetch = fetch,
): Promise<{ ok: true; scaffold: Scaffold } | { ok: false; refusal: ScaffoldRefusal }> {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) {
    return { ok: false, refusal: { reason: "malformed", status: 0, detail: "that is not a scaffold id" } };
  }
  let res: Response;
  try {
    res = await fetcher(`/api/scaffolds/${encodeURIComponent(id)}`, { cache: "no-store" });
  } catch (e) {
    return { ok: false, refusal: { reason: "unavailable", status: 0, detail: e instanceof Error ? e.message : String(e) } };
  }
  if (!res.ok) {
    const reason: ScaffoldRefusal["reason"] =
      res.status === 404 ? "not-found" : res.status === 401 || res.status === 403 ? "forbidden" : "unavailable";
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json() as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch { /* a body we cannot read is not worth a second failure */ }
    return { ok: false, refusal: { reason, status: res.status, detail } };
  }
  let body: unknown;
  try { body = await res.json(); } catch {
    return { ok: false, refusal: { reason: "malformed", status: res.status, detail: "the response was not JSON" } };
  }
  const scaffold = parseScaffold(body);
  return scaffold
    ? { ok: true, scaffold }
    : { ok: false, refusal: { reason: "malformed", status: res.status, detail: "the scaffold carried no id or no opening" } };
}

/** What the reader is told when a scaffold will not open. One sentence of state, one of what to do
 *  — never a blank chat, and never a stack trace. */
export function refusalCopy(r: ScaffoldRefusal): { title: string; body: string } {
  switch (r.reason) {
    case "not-found":
      // 404 is also the answer for "not yours": the id IS the capability until redeem binds it, so a
      // 403 would confirm to a prober that a scaffold with that id exists. The copy therefore has to
      // cover both without asserting either — and must not claim the link was "used up", which it
      // is not: reading one redeems it and it keeps resolving for its recipient.
      return { title: "This link isn't open to you.",
               body: "It may have been meant for a different address, or it may no longer exist. If it was sent to you, sign in with that address; otherwise ask whoever sent it for a fresh one — links are minted per person and are not shared." };
    case "forbidden":
      return { title: "This link belongs to someone else.",
               body: "You are signed in as a different person. Sign in with the address the mail was sent to, and it will open." };
    case "malformed":
      return { title: "This link is not readable.",
               body: "Nothing was opened rather than opening the wrong thing. Ask for a fresh link." };
    default:
      return { title: "We could not reach the service that holds this link.",
               body: "Nothing is lost — reload in a moment and it will open." };
  }
}

/** THE ONE COMPOSITION PATH (PRD §5.5 step 3). A scaffold → the fields of a chat record.
 *
 *  Pure, and separate from the fetch, so the mapping is provable without a server: this is the
 *  function that decides what the reader sees, and it is the thing most worth pinning.
 *
 *  `?ask=&meeting=` hand-links compose through HERE too, by minting a local scaffold — so there is
 *  exactly one path from "a link was clicked" to "a chat exists", not two that drift. Before this,
 *  the emailed link and the hand link built the record with different code and only one of them
 *  knew about tabs.
 *
 *  The chat's ID is the meeting's when the scaffold names one (`meet-<row>`), so a scaffold about a
 *  meeting lands in that meeting's existing conversation instead of minting a parallel one — the
 *  same rule that folded "prepare" and "DNA TSC" into one chat. */
export function scaffoldToChat(s: Scaffold, opts: { native?: string | null } = {}): {
  id: string; label: string; meeting?: string; workspaces: string[];
  artifacts: Artifact[]; focus?: string; scaffold: { kind: string; id: string };
} {
  // `native` is an INPUT, not something this function can know. `meeting:note` resolves to
  // `kg/entities/meeting/<native>.md`, and the scaffold carries the ROW id — the two are different
  // identifiers and the row id is the one the canvas binds to. The caller reads the native off the
  // meetings list it already holds. Absent, the note token resolves to nothing and the chat opens
  // one document fewer, which is the honest degradation: a tab pointing at a guessed path would
  // open a page that can never load.
  const ctx = {
    // the record's own native is authoritative; `opts` remains only for a caller that resolved it
    // some other way (it no longer has to — see Scaffold.native).
    native: s.native ?? opts.native ?? null,
    meetingId: s.meeting,
    phase: s.phase,
    mounts: s.workspaces,
  };
  const artifacts = artifactsFromTokens(s.tabs, ctx);
  const focusArt = s.focus ? artifactsFromTokens([s.focus], ctx)[0] : undefined;
  return {
    // KIND AND RECORD ID TOGETHER (F37). The chat record carries the pair or carries nothing, so
    // "an admin-setup chat with no scaffold behind it" — the shape that let a PLANTED row render
    // the pre-scaffold admin card — is not constructible. This is the only place the pair is made.
    scaffold: { kind: s.kind, id: s.id },
    id: s.meeting ? meetingChatId(s.meeting) : `scaffold-${s.id}`,
    // A meeting chat carries NO label of its own: the rail names it from the meeting, so the row
    // follows the meeting's title instead of freezing whatever the scaffold called it.
    label: s.meeting ? "" : (s.refs.title || s.openingPreset.replace(/[-_]/g, " ") || "Chat"),
    meeting: s.meeting ?? undefined,
    workspaces: s.workspaces.length ? s.workspaces : ["_global", "personal"],
    artifacts,
    focus: focusArt ? artifactKey(focusArt) : undefined,
  };
}

/** A hand link (`?ask=&meeting=`) minted into the SAME record shape, so it renders through the one
 *  composition path above. Not persisted and not a server scaffold — it is the local equivalent,
 *  which is exactly what keeps the two entry points from drifting apart.
 *
 *  `openingText` is the preset body the client substituted; a server-minted scaffold arrives with
 *  the server's substitution already done. Either way the text is MACHINERY and is marked as such
 *  by the send path — the human never sees it as their own words. */
export function localScaffold(input: {
  preset: string; openingText: string; meeting: string | null; native?: string | null;
  phase: MeetingPhase | null; workspaces: string[]; tabs: string[]; focus: string; title?: string;
}): Scaffold {
  return {
    id: `local-${input.preset}`,
    kind: "hand-link",
    meeting: input.meeting,
    native: input.native ?? null,
    phase: input.phase,
    workspaces: input.workspaces,
    refs: { title: input.title, participants: [], participantNames: {}, state: {} },
    openingPreset: input.preset,
    openingText: input.openingText,
    tabs: input.tabs,
    focus: input.focus,
    provenance: "hand link (?ask=)",
    redeemedAt: null,
  };
}
