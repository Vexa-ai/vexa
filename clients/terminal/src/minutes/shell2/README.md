# `minutes/shell2/` — the approved shell (0.13.4 lane UI-1)

Behind the feature switch **`shell2`** (registered in `core/agent/control_plane/features.py`;
explicit, needs `home2`, off by default). It brings the founder-approved mock (`cards-mock-live`,
UI decisions 7c–7hh in `UI-DECISIONS.md`) into the product, rendering only data the existing
routes already serve — no new backend.

| File | What it is |
|---|---|
| `centre.ts` | which list the centre shows besides a conversation (Inbox, Meetings); one writer, one history entry so the system back closes it |
| `SidebarNav.tsx` | the top of the sidebar as one list: + New · Inbox (count) · Meetings |
| `CentrePane.tsx` | the centre list's frame: its own header (menu control in single mode), Esc closes |
| `CentreBody.tsx` | what each list draws |
| `InboxPage.tsx`, `NeedRow.tsx`, `needs.ts` | the Inbox: grouped rows; a row's click opens the item and never decides it; Decline only on a proposal, in ⋯, behind Undo (R11) |
| `MeetingsPage.tsx`, `meetings.ts` | Meetings: Start a meeting · Send Vexa (a link sends the bot, anything else goes to an agent chat) · Live · Upcoming with Vexa joins · Past with search, filters and week/month groups |
| `about.ts`, `ChatAbout.tsx`, `Connected.tsx` | right → left (7h): "Chat about this" / "Resume chat · n" in the artifact's header; the panel holds its place when a chat opens from it; Connected lists the chats about a page as one clickable row each (7p). Which chats are about which artifact is this browser's index, filtered to chats that still exist |
| `photos.ts` | registers known people's photos so AvatarStack shows them (monogram fallback) |
| `BottomBar.tsx` | the phone's bottom bar in single mode (decision 10): Home · Inbox · Meetings · Chats |
| `useBackCloses.ts` | system back closes the drawer or the pages sheet (one history entry per open overlay) |
| `StripNav.tsx` | Inbox and Meetings on the 48px rail strip (compact and narrow windows) |
| `people.ts`, `PeoplePage.tsx` | People (founder 2026-10-17, Slack-like): everyone you share a workspace with (`GET /api/people/peers`, the default scope), merged with the interactions set (`/api/people/known`) into one row per human; scopes In my workspaces · Recent · Contacts (CRM on) · All; presence from the desktop's members |
| `PersonPanel.tsx`, `personOpen.ts`, `personProfile.ts` | the person panel (right panel / phone sheet): their own profile first (S3 seam `GET /api/people/{subject}/profile`), shared workspaces with roles, meetings together, "Notes from <org>" only as the server filters them; Message waits for S4; Invite for someone not on Vexa; your own profile with Edit (the public-profile settings) |
| `WorkspacePeople.tsx` | a workspace leads with its people: named faces with presence, who changed it last, the People section |
| `me.ts` | who "you" are, so the shell says "you" and never lists you |
| `pairRooms.tsx`, `messageRoom.ts` | S4 rooms of two: named by the other person (S4's members read) with their face, presence dot and unread count, grouped under "People" in the Rooms filter; the person panel's Message opens `POST /api/rooms/direct` once the server serves it (detected by a validation-only probe; disabled "coming" before S4) |
| `names.ts` | workspace names from the desktop the home already reads |
| `inbox.ts` | the Inbox as a pure read of the attention model (`POST /api/desktop/attention`): groups and the one count |
| `shell2.css` | the shell's styles; tokens only, scoped to these surfaces or `:root[data-shell2]` |

The sidebar filter (All · Rooms · Routines), "My desk" (`vocabulary.ts`), focus mode and the target
that follows the opened workspace (`desktop/home2/HomeTarget.tsx`, `workspaceView.ts`) live with the
code they change, each behind the same switch. With `shell2` off nothing here renders and every
surface behaves as before.
