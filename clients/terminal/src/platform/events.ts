/**
 * The terminal's window events: the names one surface dispatches and another listens for. They live in
 * the substrate, below every surface, so a surface that listens never imports the surface that
 * dispatches (the canvas ⇄ minutes folder cycle, architecture pass 4, S7).
 */

export const ASK_CHAT_EVENT = "vexa:terminal:ask-chat";

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

/** A chat turn COMMITTED to the workspace — the moment files it wrote became real.
 *
 *  A chat declares its tabs before its documents exist (PRD decision 18: the link sets the record,
 *  the panel renders the record), so the company-setup conversation opens five pages and then
 *  writes four of them over the next few turns. Without this the panel keeps showing "no page here
 *  yet" for a file that has been on disk for a minute, and the reader concludes the agent did
 *  nothing. */
export const WORKSPACE_COMMIT_EVENT = "vexa:terminal:workspace-commit";
