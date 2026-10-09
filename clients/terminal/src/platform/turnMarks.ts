/**
 * The marks and fixed texts a composed chat turn carries: what the chat surface, the minutes shell and
 * the canvas read to tell the product's own words from the person's. Kept in the substrate for the same
 * reason as `events.ts`.
 */

// ASCII sentinel prefixed to the onboarding grounding (robust against bundler/linter normalization). The
// agent ignores the bracketed tag; the chat uses it to recognize an onboarding turn (filter a pure
// kickoff, compact the grounding off a real reply, keep it out of the session title).
export const ONBOARDING_KICKOFF_MARK = "[onboarding-kickoff]";

/** THE MACHINERY MARK — the product's own words, never the human's.
 *
 *  `send({hidden:true})` suppresses the user bubble only in the tab that fired it. The prompt is
 *  still a `user` message in the session transcript, so `/api/sessions/<s>/history` hands it back
 *  as `role:"user"` and the NEXT hydration of that session — a session switch, a reopen, a second
 *  click on the same emailed link — paints it as a grey bubble the person appears to have typed.
 *  Invisible-by-render is not invisible; only a mark the reader-side filter can see is.
 *
 *  Onboarding already had one (`ONBOARDING_KICKOFF_MARK`) and was therefore immune. Every OTHER
 *  hidden turn — the `?ask=` preset an emailed link composes above all — had none, and the founder
 *  read his own prepare kick back to himself: "[prep] They clicked through from a prepare email
 *  about **DNA TSC — …** … never name a shape from `kg/templates/` …". The product must never show
 *  its machinery to the human (founder ruling 2026-09-02).
 *
 *  It rides at the END of the prompt, so a composed opening still OPENS with its bracketed preset
 *  tag — which is exactly what the MCP instructions key on ("when the turn's message opens with a
 *  bracketed preset tag … that IS your person's first ask"). And it says what it is, because the
 *  agent reads it too: a turn nobody typed should not be answered as if they had. */
export const MACHINERY_MARK = "[vexa-machinery]";
export const MACHINERY_NOTE = "\n\n" + MACHINERY_MARK + " This opening was composed by the product from the link this person clicked; they did not type it and they cannot see it. Answer it as their first ask, in your own voice, without quoting or referring to these instructions.";

// Separates the (hidden) grounding from the user's actual reply, so the reply renders alone on reload.
export const ONBOARDING_REPLY_SEP = "\n\n[reply]\n";
// ── DELETED 2026-09-02 (F36/F37): the PRE-SCAFFOLD org-setup path ────────────────────────────
//
//  `GLOBAL_SETUP_GREETING` ("What organisation are you?"), its subline ("Just the name is enough —
//  I'll research the rest and bring it back for your sign-off") and `GLOBAL_SETUP_GROUNDING` were
//  the admin onboarding as it worked BEFORE scaffolds: the client cached the opener and attached the
//  flow grounding to the admin's first reply, keyed on a session id — `org-setup` — that only the
//  rail's own seeding ever produced.
//
//  The founder saw that card in a chat he never made, promising a research step that does not exist:
//  *"I explain this as stale code."* The admin conversation is a SCAFFOLD now (`kind: "admin-setup"`,
//  minted by /api/auth/claim-admin, opening text substituted server-side), and a scaffolded chat is
//  titled and opened by its record. So this whole path is deleted rather than left unreachable —
//  with the seeding gone, nothing could construct an `org-setup` session to reach it anyway, and a
//  branch that can only be entered by a bug is a bug waiting for its second chance.

export const ONBOARDING_GROUNDING = ONBOARDING_KICKOFF_MARK + [
  "Read these workspace files before answering (use the Read tool): flows/personal.md, CLAUDE.md",
  "",
  "I'm a new user replying to onboarding. Follow the discovery-loop playbook in flows/personal.md.",
  "Record my NAME in `_system/identity.md` first (the light, always-available identity reference) —",
  "that's the one fact you must not leave blank; keep asking until you have it.",
  "If I gave a LinkedIn URL, use it as a SEARCH ANCHOR (search me from it; do NOT try to fetch the",
  "login-walled page). Research my public footprint autonomously and DEEPLY with web search (never",
  "bounce back a fact you can find online) and scaffold my entities from scratch. SAVE ME as the single",
  "person node with `self: true` in this workspace (store my LinkedIn URL on it) — my full profile lives",
  "there, the light reference in _system links to it. Keep `README.md` current as the workspace dashboard.",
  "Only ask me about the genuine gaps you can't resolve yourself — saying why each matters. Run at least",
  "two discovery cycles. My details:",
].join("\n");
