# minutes/cards — the card core's UI (0.13.4 S5)

**Concern.** How a card that waits on someone is shown and decided: as a row in the Now strip, the
Inbox and a workspace's "Waiting on you" (UI 7m); opened as a sheet (7q, 7p); inline in chat (7q);
and decided through one decision block. crm-api owns cards and enforces every rule; this folder
stores nothing durable.

**Surface.**
- `InboxCards` — the Inbox list the shell mounts (header "n waiting on you", then Waiting on you ·
  Due soon · You wait on · Decided). `NowCards` — the Now strip's count and first rows
  (`minutes/desktop/home2/NowStrip.tsx` mounts it once the Inbox answers). `RoomBadge` — a room's or
  workspace's count.
- `CardRow` — one row, two lines at most: kind icon · title · muted tail (due · workspace · room) or
  the viewer's line ("Waiting on Ana · 8 h"; folded "Signed by Ana · Thu"); one outlined action in a
  fixed place; the rest in "⋯".
- `CardSheet` / `CardSheetHost` — the opened card: a right panel on desktop, a full-screen sheet
  below 720px with ‹ Back · title · ⋯. Order: decision block (sticky) → gates → full preview → Why →
  Connected → History (each decision with a "verify" link to its revision).
- `DecisionBlock` — the viewer's actions as short equal buttons; the rule on its own line;
  "Discuss" after a gap (emits `CARD_DISCUSS_EVENT` for the shell).
- `CardInline` — the ```` ```card {"card":"<id>"} ```` fence, hooked into `ui-kit/MdxDoc.tsx`.
- `api.ts` — the one Inbox store (`useCardInbox`, `useWaitingCount`, `waitingOnMe`) and the verbs
  `seen`, `nonce`, `decide` through `POST /api/desktop/card/<op>`.
- A browser lab with synthetic cards and stubbed verbs: `/debug/cards` (`app/debug/cards`, never served in production).

## How it works

1. **One read, one count.** `useCardInbox` reads `inbox {}` once and every surface reads that store.
   Totals come from the server's `counts`; a room's badge applies the same predicate the server uses
   (`open` and `viewer.role === "next_actor"`) to that workspace's items. Nothing card-related is
   drawn until the Inbox answers.
2. **Opening.** A press on a row or a title dispatches `OPEN_CARD_SHEET_EVENT`; the first mounted
   `CardSheetHost` draws the sheet. Opening pushes one history entry, so the system back closes it.
3. **Seen.** `useSeenInView` writes `seen {card}` when the card is at least 50% visible for one
   second while the document is visible — once per card per mount, and not for a card already seen.
4. **Deciding.** The decision block fetches `nonce {card}` when it renders for a viewer with actions,
   and again when the card's revision changes. `decide` carries that nonce and content hash. A 409
   refetches the nonce, re-reads the card and says "This card changed — check it again"; a 403 shows
   crm's one line. Deliver takes one evidence reference and Answer one line; acknowledge, confirm and
   decide are one press each. From a row, Accept / Confirm / Approve act directly; Answer and
   Deliver open the sheet.
5. **Decline → Undo.** A Decline is held in this browser for 5 seconds, with Undo inline and in a
   toast (`holdDecline`, `hooks.ts`). Only when the window ends is the decision sent. The hold lives
   at module level, so closing the sheet does not cancel it; only Undo does, and then nothing reaches
   crm-api.
6. **Names.** People are named from `mentionable`, which never lists the viewer; the viewer's own id is
   learned from the cards themselves (the `next_actor` of a card whose viewer role is `next_actor`, the
   `requester` of one whose role is `requester`), so the viewer reads "you", never "Someone".
7. **Always.** Approvals and policy changes show Approve · Always · Decline. "Always" is disabled with
   the tooltip "Rules come later" until rule cards exist (S8).

## Why it complies

- **R11 — touch never decides on the first tap.** Rows and titles only open. No action appears on
  hover, so `(hover: none)` changes nothing that matters; Decline is the last item in "⋯" or in the
  sheet, and every Decline has Undo.
- **P21 / D14 — seen is a real render in view.** Written only by `useSeenInView`, never on fetch or
  hover; delivery is not seen.
- **D13 — a decision is bound to what was shown.** Every `decide` carries the nonce and content hash
  of the face on screen; replays and changed cards are refused by crm-api and the person is told.
- **7m / 7q / 7r.** Two-line rows with one outlined action in a fixed column; the sheet's decision
  block is sticky at the top with the rule on its own line; primaries are an accent outline with a
  tint, never a solid fill.
- **One source.** The Now strip, the Inbox and a room's badge read one store, so their counts cannot
  disagree (tested in `__tests__/seenAndCounts.test.tsx`).

**Dependencies.** `minutes/desktop/cardsApi.ts` (the BFF call and people's names),
`minutes/desktop/desktopApi.ts` (workspace names, no extra read), the ui-kit `Menu`, the one date
formatter and the toast. The 7g ask's pending-item routes are in `app/api/asks/`.
