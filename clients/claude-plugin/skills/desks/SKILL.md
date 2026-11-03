---
name: desks
description: Wake the person's desks from their Vexa brain. /vexa starts it by itself when the account's brain holds `desks:registry`: this chat becomes the CEO desk and starts every other desk as a fresh session that wakes only from the brain. Use when that /vexa message arrives («[Vexa] This account keeps desks…»), or when the person asks to start the CEO or the desks, for example after switching Claude accounts.
---

# Desks: the CEO and every desk, woken from the brain

The brain on the person's Vexa is the only source, and Vexa is the only way to work it. Read and write
it only through Vexa's `brain_*` tools, in this same Vexa connection. Never use local files, chat
history, another server, SSH, a loopback, an admin API or an admin key, not even to read.

When Vexa can't do a step, stop. Say in one line what is missing, file it with `report_friction`, and
don't route around it. A missing step must show up at once, not be hidden behind a workaround.

## 0. Vexa signed in first
- Before anything else, check this chat's Vexa connection. In the Claude app, use
  `session_connectors_status` and look at the `vexa` row.
- If it needs a sign-in, your first and only words are one prompt: sign in to Vexa in this chat (in the
  Claude app: **Reconnect vexa**, or `/mcp` → vexa) with the same Google account the Vexa panel uses.
  Then wait.
- Once it is connected, call `whats_waiting` and check that `connected_as` is the panel's account.
  If it is another account, stop and say so in one line: the chat and the panel would act as two
  different people.
- Then the line from Vexa and the panel. `/vexa` starts both. If this skill started some other way,
  start them here:
  - the line: `listen_token`, then `vexa-link` in the background, never showing the key;
  - the panel: `panel_link`, opened in the side pane.
- **Start the desks only after this sign-in.** A session started before the sign-in keeps a signed-out
  Vexa, and no agent can re-dial it.
- Each desk still checks its own connection first and reports a missing sign-in in one line.
- If Vexa drops later in the run, that is a stop, not a detour: a tool that answers with a sign-in
  error means you say so in one line and ask for the sign-in again.

## 1. Account
- Note `connected_as` from `whats_waiting`: the account and the address.
- Say in one line which account this chat runs as.

## 2. The registry and the CEO desk
- `brain_object("desks:registry")`: the desks, one row each, with role, name, doc and page.
  No such object means this account has no desks: say so in one line and stop.
- `brain_object` on the `ceo` row's doc, and the objects it links: rulings, what waits on the person,
  state, the desks, lessons. That is this chat's whole context.
- The newest plan it links (`plans:<date>`): the plan cards each desk is delivering.
- This chat is now the **CEO desk**. Open the `ceo` row's page in the side pane:
  `panel_link(next="/panel/o/<page>")`. In the Claude app, open that link with `preview_start`.
  Then read the same page with `brain_page_view`. The desks' gate (step 3) holds for this chat too:
  no Vexa, no open page or no read means stop and say which, in one line.
- This chat's own session id: in the Claude app, `get_window_layout` → `this_session.session_id`.

## 3. Start the desks
1. Every desk starts as a **fresh** session that wakes from the brain.
   - Never reuse a desk session from before this /vexa: it carries old context and may belong to
     another Claude account.
   - Start each desk once per /vexa.
2. For every registry row except `ceo`, start a session (in the Claude app: `start_session`) with:
   - title `<Name> desk`, name `desk-<role>`, a fresh context, no worktree, the row's `cwd` if it has
     one, the row's `model` if it has one, else this chat's model
   - background: «Vexa desk for <connected_as>. Wake only from the brain through Vexa's brain_* tools.
     The CEO desk is <this session's id>.»
   - prompt:
     > You are the <Name> desk of Vexa for <connected_as>.
     >
     > **Gate. You do nothing until all three hold:**
     > 1. Vexa is connected in this chat (`whats_waiting` answers, as <connected_as>);
     > 2. your desk page is open in your side pane: `panel_link(next="/panel/o/<page>")`, kept open;
     > 3. you have read the same page through Vexa: `brain_page_view("<page>")`, and for every widget
     >    the rows you see in your pane are the rows it returned. A widget whose rows differ (the agent
     >    sees something the person doesn't, or the other way round) is a stop: say which one, and file it
     >    with `report_friction`.
     >
     > If any of the three is missing, stop. Say in one line which one, here and to the CEO desk, and
     > wait. Check the gate again before each decision, and at once when a Vexa tool answers with a
     > sign-in error. Never work on without it.
     >
     > Each time the gate passes, write it down: `brain_fact` on `<doc>`, kind `gate`, body
     > `{connected_as, page, read_at, words}`, source_uri `claude-session:<your session id>`.
     >
     > Wake ONLY from the brain: `brain_object("<doc>")` and the objects it links. That is your whole
     > context: scope, rulings, state, in flight, what waits on the person, next moves, lessons.
     >
     > Your desk page is one projection for the person and for you: the panel draws it, and
     > `brain_page_view("<page>")` returns the same model with its measures, folded the same way.
     > Your scope is what it shows; work outside it belongs to another desk.
     >
     > Work only through Vexa. When Vexa can't do a step, stop, say what is missing in one line, and
     > file it with `report_friction`. Pass `as_role: "<role>"` on every brain write.
     >
     > Carry on with your next moves. Keep `<doc>` current on every change of state
     > (`brain_document_upsert`).
     >
     > Before you call a page a person reads done, or hand out its link: read it back with
     > `brain_page_view(<key>)`. Its `measures.over` must be empty; shorten and re-read until it is. Then
     > look at it in your side pane at ~1500 px: at most two screens, and the first screen holds what the
     > reader needs.
     >
     > The budget measures form, not use. At wake and after each change to your page, also answer the
     > person's four questions about it, each with what on the page shows it:
     > 1. Can they tell in 5 seconds what is happening and what needs them?
     > 2. Is the most useful thing at the top, and open?
     > 3. Is there no wall of repeated text?
     > 4. Is nothing cut off or unreadable?
     >
     > Record the answers with `brain_fact` on `<doc>`, kind `usefulness`. A «no» is fixed before
     > anything else on the page. Every number on it is current, or shows that it is not.
     >
     > Questions for the person go through the CEO desk, one at a time, never to them directly.
     > Nothing goes out in their name: drafts only, until they say it goes.
     >
     > When the person answers a decision (in your chat or through the CEO desk): record it with
     > `brain_decision`, using their exact words and your chat as the source. Then, if it closes the card,
     > write `brain_fact` on the card, kind `stage_evidence`, body `{"stage": "Done"}`, with source_uri
     > the decision's key. If it only opens a next step, move the card's Next instead. A card they have
     > answered leaves their view on its own.
     >
     > Report to the CEO desk in 3 lines once you are awake.
   - Where the app cannot start sessions, list each desk with its prompt in one table and stop there.
3. Record the sessions on every run:
   - each new session id on the `ceo` doc: `brain_fact` with kind `session`, body `{role, session_id}`,
     source_uri `claude-session:<session id>`;
   - the whole map, CEO included, in `source:factory/desk-sessions`: `brain_document_upsert`, body
     JSON `{role: session id}`, so any desk can find any other.
4. In the Claude app, file the desks in the sidebar on every run, without asking:
   - rename this chat `CEO desk`;
   - move this chat and every new desk into the sidebar group **Desks** (create it only if it is
     missing);
   - pin each of them after the move (a move into a group unpins);
   - sort the pinned section by name;
   - unpin desk sessions left from an earlier run.
5. Once a desk's replacement has reported awake, archive the session it replaces. Name in one line
   any other old desk session still open.

## 4. Then work as the CEO
- Desk reports arrive as messages in this chat.
- Bring the person only what is theirs to decide, one question at a time, each standing on its own.
- Keep the `ceo` doc current on every change.

When Vexa offers `desks()` and `handoff(role)`, use them in place of `desks:registry` and the raw doc reads.
