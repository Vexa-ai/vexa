# minutes/__tests__

Unit tests for the minutes shell, at function boundaries — the flat-rail chat registry
(railChats), proposal-chip handler contract (proposals: rebind/relabel/fire in place, never
append), link-target resolution (linkTargets), collapse persistence, the doc header, and the pages panel's tabs (pagesTabs, stripHistory —
Obsidian's rule: navigation takes the one preview slot, a pin makes a tab). House
style: pure functions over DOM where a boundary exists; vitest.

`browserSessionView.test.tsx` — the live sign-in view: password only in the fragment, status polling to "saved", Close deletes the session, and the two ways it opens (a `browser_session_open` result, `startBrowserSession`).

`botAccounts.test.tsx` — the Bot accounts section: list with owner and state, read-only company accounts outside the admin account, sign in / sign in again / confirmed delete.
