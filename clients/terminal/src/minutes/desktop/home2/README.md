# terminal/src/minutes/desktop/home2 — the attention home and rich pages (#1783, R3)

Behind the `home2` feature (off by default). Approved mocks: proto-on-product 96a5f50a4 (home) and
382a319b5 (workspace page).

- `StatusCard.tsx` — a workspace on the home: needs you (up to two, with their act), working here, or the
  calm line. A click aims the home composer (never a read-only workspace) and opens the workspace page.
- `NowStrip.tsx` — live calls, the next call within a day, the first things waiting on you.
- `HomeTarget.tsx` — the sticky home composer in the target workspace's colour, and the workspace mark.
- `WorkspacePage.tsx` — the workspace page in the right panel: now, needs you, rooms, your chats,
  routines, calls, cards board, files, people, activity, metrics. Empty sections are hidden; Expand
  widens the panel.
- `attentionApi.ts` — the client half of E1: the last answer of `POST /api/desktop/attention`, kept fresh.
- `acts.ts` — what an item's act or a page action does, always as the person who clicks, through the
  owner's route; anything that reaches beyond their screen is confirmed first. A page action's
  target is resolved for the viewer (`/api/desktop/refs`, or their own attention list for a
  proposal) before anything runs: an id of the wrong shape, or one they do not own or cannot see, is
  refused. The confirm names what was resolved, never the page's label, and a routine run shows in
  the chat as exactly the line sent. `join-call` joins only a call already live; otherwise it is a
  confirmed start.
- `richParse.ts` — the page grammar (typed references, badges, blocks, allow-listed actions, view specs)
  and HTML sanitizing. Pure.
- `RichPage.tsx`, `ViewBlock.tsx`, `LiveTable.tsx` — the renderer for pages in the right panel.
- `workspaceView.ts` — the one seam for "open this workspace".
- `settingsApi.ts` — the person's own settings about each workspace (pinned, hidden from home, muted,
  notification level, room order, folded sections): the CRM `member_of` relation, written only by
  crm-api through `/api/desktop/workspace-settings`. Never browser storage.

Documents never open in the centre (UI decision 7c).
- `NewWorkspace.tsx` — making a workspace (mock flow 12): private / shared (/ open while
  `open_workspaces` is on), a seeded rich README, the identity PROPOSED (a site's logo, or the agent
  designs a mark) and accepted on the new card, and invitations drafted in the person's chat — never sent.
