# terminal/src/minutes/desktop — the desktop on the home (#1783, desktop ADR D1)

Behind the `desktop` feature. The home keeps its greeting and composer; below them the person's
desktop replaces the old composed cards (UI decisions 15a, 15b, 15e). Off, refused by crm, or not
readable: the current home, untouched.

- `DesktopHome.tsx` — the one guarded insertion the shell makes, and `DesktopToaster` (the Undo toasts).
- `DesktopView.tsx` — sections in the person's order (Workspaces, Agents, Files, Cards; Cards only while
  `desktop_cards` is on), collapse, hide and restore, section and tile drag, the "Vexa suggests" rows
  (desk arrangements and card proposals), card invites, filter chips.
- `DesktopTiles.tsx` — workspace tiles (15b), agent and file tiles (15e), `missing` as "no longer available · Remove".
- `desktopApi.ts` — the one read (`GET /api/desktop`) and the arranging verbs. Every hand arrangement
  writes the same ops the agent's `desktop_arrange` writes, with `base_version`; a `desk_changed`
  re-reads and retries once; every applied change offers Undo.
- `useDesktopItems.ts` — "Add to desktop" / "Remove from desktop" for the chat, routine and file menus, and the drag payload a chat-list or file-tree row carries onto the desktop.

Cards (D2, feature `desktop_cards`):
- `CardFace.tsx` — the card tile and its face, drawn from the resolved blocks; `faceBlocks.ts` sanitizes
  the face again (the blocks.v1 face profile: unknown blocks dropped, caps, plain text only).
- `CardDetail.tsx` — the card opened (read fresh from `/card/read`: stage moves to declared states only,
  description, activity, sub-cards, members, Add member) and the one dialog host for `OPEN_CARD_EVENT`.
- `CardRows.tsx` — "Vexa suggests … · Add · Not now" (review) and "… added you to … · Accept · Decline".
- `cardsApi.ts` — the person's card verbs through `/api/desktop/card/<op>`; names for members.
- `useCardItems.ts` — "Make a card" for the chat, page and file menus.

The read model itself (`composeDesktop`) is `../../platform/desktop.ts`; the BFF is `../../app/api/desktop/`.
Nothing about the desktop is stored in the browser.
