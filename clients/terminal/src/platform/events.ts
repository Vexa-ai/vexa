/**
 * The terminal's window events: the names one surface dispatches and another listens for. They live in
 * the substrate, below every surface, so a surface that listens never imports the surface that
 * dispatches (the canvas ⇄ minutes folder cycle, architecture pass 4, S7).
 */

export const ASK_CHAT_EVENT = "vexa:terminal:ask-chat";

/** OPEN THIS CHAT — the server started a new shared chat (an @-mention shared a private one,
 *  ADR-0048) and the composer hands the shell its id. `detail.session` is the store id. */
export const OPEN_SESSION_EVENT = "vexa:terminal:open-session";

/** A user-authored send. The minutes rail listens: a chat nobody has written in is exactly what
 *  its default filter hides, and this is the cheap write that makes `touched` true — no history
 *  fetch, no heuristic. `detail.session` IS the chat id. */
export const CHAT_TOUCHED_EVENT = "vexa:terminal:chat-touched";

// Clicking an entity link in chat (a [[wikilink]] or a kg/entities/*.md path) dispatches this; the
// workbench resolves it to a file and opens the doc (revealing the center if in chat-only mode).
export const OPEN_ENTITY_EVENT = "vexa:terminal:open-entity";

// A `vexa-meeting:<platform>/<native>` link in a meeting note dispatches this; the workbench resolves
// the native id → the meeting row and opens its canvas (transcript + recording). Detail: { ref }.
export const OPEN_MEETING_EVENT = "vexa:terminal:open-meeting";

// ── DELETED 2026-09-02: ONBOARDING_SEED_EVENT and COMPANY_LAYER_EVENT ────────────────────────
//
//  The first seeded a CACHED greeting into a brand-new chat; the second told the rail when to plant
//  its two structural rows. The founder opened a rail holding three chats he never made, greeted by
//  text he never asked for: *"where is it coming from? i did not create this chat, and i do not like
//  this text."* Both events existed only to make those two things happen, so both are gone rather
//  than left dangling with no dispatcher — a listener nobody fires is the stale-code shape he ruled
//  on in the same session (F37). A chat opened with `+` shows an empty composer and nothing else.

/** A FILE THE TURN JUST WROTE, offered to the pages panel (F41).
 *
 *  The chat surface hears it on the stream and re-emits it here rather than opening anything
 *  itself: the tab set is part of the CHAT RECORD (PRD decision 18), the minutes shell is that
 *  record's one writer, and a second opener would be a second writer of the same surface. Same
 *  seam, and for the same reason, as OPEN_ENTITY_EVENT. */
export const ARTIFACT_EVENT = "vexa:terminal:artifact";

/** SOMEBODY ASKED TO SEE SOMETHING — a successful `open_page` (Vexa-ai/vexa#1586).
 *
 *  Deliberately NOT the same event as ARTIFACT_EVENT, though both end in the same view slot. An
 *  artifact is the TURN saying "I wrote this", which must stand down in front of a reader who has
 *  opened something else; this is the READER'S OWN ASK coming back, so it always wins. Folding the
 *  two would mean one flag deciding both, and the flag would be wrong for one of them.
 *
 *  Same seam and same reason as ARTIFACT_EVENT otherwise: the chat surface hears it on the stream
 *  and re-emits it rather than opening anything itself, because the panel's state is part of the
 *  chat record (PRD decision 18) and the minutes shell is that record's one writer. */
export const OPEN_PAGE_EVENT = "vexa:terminal:open-page";

/** THE PERSON CHOSE WHERE THIS CHAT WRITES (UI decision 6b) — their Switch on an agent's
 *  `propose_write_target`. Detail `{ session, workspace }` (`""` = Personal). The minutes shell, the one
 *  writer of a chat's target on this side, applies it exactly as a chip click does. Never dispatched
 *  for anything the agent did on its own. */
export const CHOOSE_TARGET_EVENT = "vexa:terminal:choose-target";

/** WHAT THIS CHAT'S AGENT HAS WRITTEN — the files its turns wrote, with the turn's time (founder,
 *  2026-10-15: *"right sidebar artefacts belong to the agent on the left side of it"*). The chat
 *  surface owns the turns and re-emits this whenever they change; the minutes shell lists it in
 *  the panel's own view of the chat. Detail: `{ session, turns: { role, at, ops: { file, wrote }[] }[] }` (agent turns that wrote). */
export const CHAT_ARTIFACTS_EVENT = "vexa:terminal:chat-artifacts";

/** A WORKSPACE THE TURN CREATED, JOINING THE CHAT'S FOCUS (Vexa-ai/vexa#1603).
 *
 *  The founder asked for *"a new workspace where we will collect everything we know about Copperline"*,
 *  got one, and was told *"the new workspace isn't in my native mount stack (it's reached via the
 *  workspace_* tools)"* — *"not native workspace??"*. Creating a place IS bringing it into the
 *  room, so the create moves the chip and the panel, exactly as a send moves the transcript.
 *
 *  A THIRD event rather than an artifact or an open, because it names a WORKSPACE and those two
 *  name a PAGE: the shell's answer here is to widen the chat's mount set, not to front a document.
 *  Same seam as both otherwise — the chat surface hears it on the stream and re-emits it, and the
 *  minutes shell, the one writer of the chat record (PRD decision 18), decides what happens. */
export const FOCUS_WORKSPACE_EVENT = "vexa:terminal:focus-workspace";

/** SHOW A WORKSPACE'S FILES (#1783, desktop): `{ workspace, name? }` — the workspace's root as a
 *  folder listing in the pages panel. A desktop tile sends it for a workspace with no README, so the
 *  listing can offer Create README. It names a workspace and opens nothing in the chat: no mount, no
 *  target, no session. */
export const OPEN_WORKSPACE_FILES_EVENT = "vexa:terminal:open-workspace-files";

/** A chat turn COMMITTED to the workspace — the moment files it wrote became real.
 *
 *  A chat declares its tabs before its documents exist (PRD decision 18: the link sets the record,
 *  the panel renders the record), so the company-setup conversation opens five pages and then
 *  writes four of them over the next few turns. Without this the panel keeps showing "no page here
 *  yet" for a file that has been on disk for a minute, and the reader concludes the agent did
 *  nothing. */
export const WORKSPACE_COMMIT_EVENT = "vexa:terminal:workspace-commit";

/** A PERSON DELETED A PAGE (founder 2026-10-11: *"we probably need a delete for a note"*). Detail:
 *  `{ path, slug }` — `slug` absent for the reader's own desk. The minutes shell, the one writer of
 *  the chat record, drops every tab on that page; the delete itself has already committed. */
export const PAGE_REMOVED_EVENT = "vexa:terminal:page-removed";

/** A `terms` event, re-emitted onto the window by the chat surface and heard by the canvas (`canvas/transcriptTerms`) — the same seam, and for the
 *  same reason, as ARTIFACT_EVENT: the stream reader owns no state and the shell owns no parsing. */
export const TERMS_EVENT = "vexa:terminal:terms";

/** START (OR JOIN) THE CALL IN THIS CHAT — an @-picked person was invited to a call (founder
 *  2026-10-15: "@ is to tag and to invite to a call"). `detail.session`; the shell enters the call,
 *  and everyone already in the chat is rung (`call_started`). */
export const CALL_START_EVENT = "vexa:terminal:call-start";

/** THE AGENT STARTED A CALL FOR THIS PERSON — a successful `call_person` ("Call tester-b"),
 *  re-emitted by the chat surface only in the view whose own message triggered it, and only live.
 *  Detail `{ session, room, with, alreadyLive }` (`minutes/callPlaced.ts`). The shell opens the room
 *  and joins as the caller, shown as their outgoing call with Cancel — meeting-api ends a call nobody
 *  joins within 30 s, so waiting for the person to open the room themselves lost the call. */
export const CALL_PLACED_EVENT = "vexa:terminal:call-placed";

/** START A NEW CHAT AT A MODEL LEVEL (hosted plans, `levels`): the composer's locked "Level N" menu
 *  asks for it. `detail.level` 1–5; the shell opens a new chat and sets that level on it, then emits
 *  LEVEL_SET_EVENT so the new chat's picker shows it. */
export const NEW_CHAT_AT_LEVEL_EVENT = "vexa:terminal:new-chat-at-level";
/** A chat's level was set from outside its picker: `detail` {session, level}. */
export const LEVEL_SET_EVENT = "vexa:terminal:level-set";
/** AIM THE HOME COMPOSER (#1783, R3 `home2`): a workspace status card was clicked, so the new chat in
 *  front writes there. `detail.workspace` is the workspace id; "" is the person's own (Personal). A
 *  read-only workspace never sends it. The shell sets the target through its one writer. */
export const HOME_TARGET_EVENT = "vexa:terminal:home-target";

/** "New chat" on a workspace's page (founder 2026-10-16: it was a dead click): a fresh chat in the
 *  centre, aimed at `{workspace}`, its composer focused; `draft` prefills the composer. */
export const NEW_CHAT_AT_EVENT = "vexa:terminal:new-chat-at";
/** What a room is showing, read with its lines (JTBD-1 "Show to room"): the room's members open it. */
export const ROOM_SHOWING_EVENT = "vexa:terminal:room-showing";

/** "New room" (a workspace page, the + New menu): make a room in `{workspace}` (founder 2026-10-16). */
export const NEW_ROOM_EVENT = "vexa:terminal:new-room";
/** START A MEETING (shell2, the Meetings page, UI 7w): a new chat with a call in it, filed in My desk
 *  — "a call can be just a call", filed later. The shell owns the chat and the call. */
export const START_MEETING_EVENT = "vexa:terminal:start-meeting";
