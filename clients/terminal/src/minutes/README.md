# minutes/ — the Minutes shell

The browser app people use day to day: three columns — the **chats** rail on the left (a meeting is a
chat too), the **conversation** with the agent in the middle, and the **pages** it reads and writes on
the right. Minutes adds no logic of its own; every action goes through the documented APIs. The
person-facing guide is [`docs/docs/minutes/`](../../../../docs/docs/minutes/overview.mdx).

By concern:

- **Frame** — `MinutesShell.tsx` (the three columns, folding and resizing), `Rail.tsx` and `chats.ts`
  (the chats rail: chats and meetings in one list, rows keyed `c:<id>` / `m:<id>`), `chatOrder.ts`
  (the saved rail order), `ChatName.tsx` (renaming), `ContextBar.tsx` (the chat's name, kind and
  workspace chips), `AccountBadge.tsx`, `Collapse.tsx`, `roomView.ts` (which page is in front), `deepLink.ts` and `arrival.ts`
  (`?ask=` / `?meeting=` / `?view=` links and the first visit), `scaffold.ts`,
  `ScaffoldRefusalCard.tsx`.
- **Pages** — `PagesPanel.tsx`, `Navigator.tsx` and `navigatorApi.ts` (the workspaces and files list),
  `MarkdownEditor.tsx` (editing in place), `assetDrop.ts` (images), `mermaidPreview.ts`,
  `WorkspaceReadmePanel.tsx`, `workspaceReadme.ts` and `workspaceFrontPage.ts` (a workspace's front
  page), `ExtendAction.tsx` and `extend.ts` (Extend and Create as background jobs; a text selection's
  **Ask about this**, which drafts a quote into the chat), `machinery.ts` (what a reader is not shown
  as files), `deskPanel.ts` and `deskTouch.ts` (the right panel when a chat names no page).
- **Meetings** — `MeetingPageHeader.tsx`, `MeetingControls.tsx`, `meetingNote.ts`,
  `RecordingPlayer.tsx` and `meetingPlayback.ts` (playback that follows the transcript).
- **Chat helpers** — `ProposalChips.tsx` and `proposals.ts` (suggested next moves),
  `FlowProposalAct.tsx`, `PoliciesAct.tsx`.
- **Connections and workspaces** — `ConnectionsPanel.tsx`, `OAuthConnectionForm.tsx`,
  `SecretConnectionForm.tsx`, `DestinationHost.tsx` and `connectionHosts.ts` (a secret's destination host and its first-use
  confirmation), `connectionEvents.ts`,
  `connectionStyles.ts`, `GitConnection.tsx`, `AttachRepo.tsx`.
- `vocabulary.ts` (the product's words for its surfaces), `tokens.ts` (design tokens), `types.ts`,
  `mockPhases.ts` (layout fixtures); tests in `__tests__/`.
