---
name: desks
description: Wake the person's desks from their Vexa brain. /vexa starts it by itself when the account's brain holds `desks:registry`: this chat becomes the CEO desk and starts every other desk as a fresh session that wakes only from the brain. Use when that /vexa message arrives («[Vexa] This account keeps desks…»), or when the person asks to start the CEO or the desks, for example after switching Claude accounts.
---

# Desks: the CEO and every desk, woken from the brain

The brain on the person's Vexa is the only source. Read it only through Vexa's `brain_*` tools, in this
same Vexa connection; never from local files, chat history or another server. If `brain_object` is not
in this chat, Vexa is not connected here: say so in one line and point at `/vexa signin`.

## 1. Account
- Call `whats_waiting`. Note `connected_as`: the account and the address.
- Say in one line which account this chat runs as.

## 2. The registry and the CEO desk
- `brain_object("desks:registry")`: the desks, one row each, with role, name, doc and page.
  No such object means this account has no desks: say so in one line and stop.
- `brain_object` on the `ceo` row's doc, and the objects it links: rulings, what waits on the person,
  state, the desks, lessons. That is this chat's whole context.
- The newest plan it links (`plans:<date>`): the plan cards each desk is delivering.
- This chat is now the **CEO desk**. When no call is live, open the `ceo` row's page in the panel:
  `panel_link(next="/panel/o/<page>")`.

## 3. Start the desks
1. Every desk starts as a **fresh** session that wakes from the brain.
   - Never reuse a desk session from before this /vexa: it carries old context and may belong to
     another Claude account. If the app lists old desk sessions still open, name them in one line and
     offer to archive them.
   - Start each desk once per /vexa.
2. For every registry row except `ceo`, start a session (in the Claude app: `start_session`) with:
   - title `<Name> desk`, name `desk-<role>`, a fresh context, no worktree, the row's `cwd` if it has
     one, the row's `model` if it has one, else this chat's model
   - background: «Vexa desk for <connected_as>. Wake only from the brain through Vexa's brain_* tools.
     The CEO desk is <this session's id>.»
   - prompt:
     > You are the <Name> desk of Vexa for <connected_as>.
     >
     > Wake ONLY from the brain: `brain_object("<doc>")` and the objects it links. That is your whole
     > context: scope, rulings, state, in flight, what waits on the person, next moves, lessons.
     >
     > Open your desk page with `panel_link(next="/panel/o/<page>")`.
     >
     > Carry on with your next moves. Keep `<doc>` current on every change of state.
     >
     > Questions for the person go through the CEO desk, one at a time, never to them directly.
     > Nothing goes out in their name: drafts only, until they say it goes.
     >
     > Report to the CEO desk in 3 lines once you are awake.
   - Where the app cannot start sessions, list each desk with its prompt in one table and stop there.
3. Record each new session id on the `ceo` doc: `brain_fact` with kind `session`, body
   `{role, session_id}`, source_uri `claude-session:<session id>`.

## 4. Then work as the CEO
- Desk reports arrive as messages in this chat.
- Bring the person only what is theirs to decide, one question at a time, each standing on its own.
- Keep the `ceo` doc current on every change.

When Vexa offers `desks()` and `handoff(role)`, use them in place of `desks:registry` and the raw doc reads.
