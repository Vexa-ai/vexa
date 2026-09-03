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
import { artifactKey, homeEntry, meetingChatId, withHome, type Artifact } from "./chats";
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
  /** WHERE THE MEETING'S RECORD LIVES, said by the server that writes it — never constructed
   *  here. `drop_to_attendees` writes `kg/entities/meeting/<meeting-day>-<title-slug>.md`; this
   *  client used to point `meeting:note` at `kg/entities/meeting/<native>.md`, a second spelling
   *  that matched nothing. Absent, the note token DROPS rather than guessing again. */
  notePath?: string;
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
  /** THE STRIP THE LINK DELIVERS (decision 28.5). Entries in order, left to right. An entry may be
   *  a bare token (history) or carry `pinned: true` (a chat pin, held at the left edge and immune
   *  to aging). `focus` names the one the view opens on. */
  tabs: { token: string; pinned: boolean }[];
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

/** `tabs` on the wire: a list whose entries are either a bare token or `{token, pinned}`.
 *
 *  BOTH shapes, deliberately. The presets' frontmatter is a file the founder edits, and
 *  `tabs: meeting:note, meeting:transcript` must keep working — asking a human to write
 *  `{token: …, pinned: false}` for the common case would be the format serving the parser. A
 *  string is history; the object is how a preset says "and keep this one". */
function tabsOf(v: unknown): { token: string; pinned: boolean }[] {
  if (!Array.isArray(v)) return [];
  const out: { token: string; pinned: boolean }[] = [];
  for (const e of v) {
    if (typeof e === "string" && e.trim()) { out.push({ token: e.trim(), pinned: false }); continue; }
    if (e && typeof e === "object") {
      const o = e as Record<string, unknown>;
      const token = typeof o.token === "string" ? o.token.trim() : "";
      if (token) out.push({ token, pinned: o.pinned === true });
    }
  }
  return out;
}

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
      notePath: str(refsRaw.note_path) || undefined,
    },
    openingPreset: str(r.opening_preset),
    openingText,
    tabs: tabsOf(r.tabs),
    focus: str(r.focus),
    // `provenance` on the wire is the OBJECT {flow, step, reaction_id, minted_by}; the string is
    // `provenance_line`. Reading the object through `str()` degraded to "" silently — four facts
    // cannot be a string, and it is the line we want to show.
    provenance: str(r.provenance_line),
    redeemedAt: str(r.redeemed_at) || null,
  };
}

const SCAFFOLD_ID = /^[A-Za-z0-9_-]{1,128}$/;


/** THE TRANSCRIPT SHARE, redeemed AGAINST THE SCAFFOLD ID (R-A08).
 *
 *  A scaffold about a meeting that is not the reader's own carries a restricted grant on that
 *  meeting's transcript, minted by its owner. It used to ride the mailed link as `&tshare=<token>`
 *  — a bearer credential in a query string, which enters every access log and proxy trace between
 *  us and the recipient's inbox, and then whatever they forward. `core/agent/worker/engine.py`
 *  states the rule for the MCP delegation token in as many words: *the token travels in a header,
 *  never in the URL*. The link is now an id and nothing else, and this asks for the capability over
 *  the authenticated session that already proved who the reader is.
 *
 *  NEVER THROWS, and null is the ordinary answer: most scaffolds are about the reader's own meeting
 *  and carry no share at all. A caller that treated null as breakage would show an error on the
 *  common path — and the one thing worse than a missing capability is a person told their link is
 *  broken when it is not. */
export async function redeemScaffoldShare(
  id: string,
  fetcher: typeof fetch = fetch,
): Promise<string | null> {
  if (!SCAFFOLD_ID.test(id)) return null;
  try {
    const res = await fetcher(`/api/scaffolds/${encodeURIComponent(id)}/share`, {
      method: "POST", cache: "no-store",
    });
    if (!res.ok) return null;
    const body = await res.json() as { token?: unknown };
    return typeof body?.token === "string" && body.token ? body.token : null;
  } catch {
    // A share we could not fetch is a meeting the reader may not see yet — worth a retry on the
    // next open, never worth failing the arrival over. The chat still opens.
    return null;
  }
}


/** Fetch one scaffold AS THE SIGNED-IN IDENTITY. The server decides whether it is theirs; the
 *  client never asserts who it is for. A refusal is returned, never thrown — the caller renders a
 *  card that states the situation. */
export async function fetchScaffold(
  id: string,
  fetcher: typeof fetch = fetch,
): Promise<{ ok: true; scaffold: Scaffold } | { ok: false; refusal: ScaffoldRefusal }> {
  if (!SCAFFOLD_ID.test(id)) {
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
 *  — never a blank chat, and never a stack trace.
 *
 *  NAME THE IDENTITY IT WAS JUDGED AGAINST (F48). "This link isn't open to you" answers a question
 *  the reader did not ask — they know it is not opening — and withholds the one fact that explains
 *  it: WHICH account the server just measured the link against. Nine of these in ten are a person
 *  signed in with their second address, and they cannot see which one that is from anywhere in this
 *  app. So the copy states the address, and `offerSwitch` tells the card to render the way out;
 *  telling somebody they are on the wrong account without a door to the right one is half an
 *  answer. `signedInAs` is optional because the identity probe can fail — the copy then degrades to
 *  what it said before, never to "signed in as undefined". */
export function refusalCopy(r: ScaffoldRefusal, signedInAs?: string | null): {
  title: string; body: string; offerSwitch: boolean;
} {
  const who = (signedInAs || "").trim();
  switch (r.reason) {
    case "not-found":
      // 404 is also the answer for "not yours": the id IS the capability until redeem binds it, so a
      // 403 would confirm to a prober that a scaffold with that id exists. The copy therefore has to
      // cover both without asserting either — and must not claim the link was "used up", which it
      // is not: reading one redeems it and it keeps resolving for its recipient.
      // The address is NAMED but nothing is asserted about the link: on a 404 we genuinely do not
      // know whether it went elsewhere or is gone, and saying either would be the confirmation the
      // 404 exists to withhold. "You are signed in as X" is ours to state — it is the reader's own
      // session, not a fact about the link — and it is the half they cannot see for themselves.
      return { title: "This link isn't open to you.",
               body: (who ? `You are signed in as ${who}. ` : "")
                 + "This link may have been meant for a different address, or it may no longer exist. Sign in with the address it was sent to, or ask whoever sent it for a fresh one — links are minted per person and are not shared.",
               offerSwitch: true };
    case "forbidden":
      // A 403 IS the verdict "not yours", so here the sentence can be flat.
      return { title: "This link belongs to someone else.",
               body: who
                 ? `You are signed in as ${who}; this link was sent to another address. Sign in with that address and it will open.`
                 : "You are signed in as a different person. Sign in with the address the mail was sent to, and it will open.",
               offerSwitch: true };
    case "malformed":
      return { title: "This link is not readable.",
               body: "Nothing was opened rather than opening the wrong thing. Ask for a fresh link.",
               offerSwitch: false };
    default:
      return { title: "We could not reach the service that holds this link.",
               body: "Nothing is lost — reload in a moment and it will open.",
               offerSwitch: false };
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
  // THE NOTE'S PATH IS AN INPUT TOO, and for the same reason `native` is: this function cannot
  // know it. It is `kg/entities/meeting/<meeting-day>-<title-slug>.md`, where the day is rendered
  // in the ORGANISER's timezone and the slug through a server-side allow-list — neither is
  // derivable out here, and deriving it anyway is exactly the bug this replaces. The scaffold
  // carries `refs.note_path` from the step that writes the file. Absent, the note token resolves
  // to nothing and the chat opens one document fewer, which is the honest degradation: a tab
  // pointing at a guessed path opens a page that can never load.
  const ctx = {
    // the record's own native is authoritative; `opts` remains only for a caller that resolved it
    // some other way (it no longer has to — see Scaffold.native).
    native: s.native ?? opts.native ?? null,
    notePath: s.refs.notePath ?? null,
    meetingId: s.meeting,
    phase: s.phase,
    mounts: s.workspaces,
  };
  // THE SCAFFOLD DELIVERS THE STRIP (decision 28.5), and the order at open is:
  //   the chat's HOME · the scaffold's PINS · the scaffold's opening pages · (later) history.
  // `withHome` puts the first tier in place; `artifactsFromTokens` preserves the preset's order
  // for the rest, which is the author's reading order.
  const declared: Artifact[] = [];
  s.tabs.forEach((t, i) => {
    const a = artifactsFromTokens([t.token], ctx)[0];
    // `at` is the preset's own order: the author's reading order becomes the strip's history order,
    // so the last declared page sits nearest the current one.
    if (a) declared.push({ ...a, pinned: t.pinned ? true : undefined, at: i + 1 });
  });
  const artifacts = withHome(declared, s.workspaces);
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
    // A scaffold that names no focus opens on the chat's HOME — a chat that opens on nothing is a
    // chat that opens on a blank panel (decision 26.4).
    focus: artifactKey(focusArt ?? homeEntry(s.workspaces)),
  };
}

/*  `localScaffold` LIVED HERE AND IS DELETED (F97, decisions 13/18).
 *
 *  It built a scaffold record in the BROWSER so the `?ask=&meeting=` hand link could render through
 *  the same composition path as an emailed one. That was the right instinct about the path and the
 *  wrong place for the record: composing it client-side meant the opening was substituted from the
 *  URL, so `/?ask=prep&meeting=<payload>` put attacker-chosen text into the agent's first turn.
 *
 *  The hand link now mints SERVER-side (`POST /api/scaffolds/hand`) and redirects to `/?s=<id>`, so
 *  there is still exactly one composition path — it just runs where the facts are. Deleted rather
 *  than left unused: a constructor that can build a record out of untrusted input is a loaded gun
 *  once nothing calls it and nobody remembers why. */
