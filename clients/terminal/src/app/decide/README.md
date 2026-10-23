# terminal/src/app/decide

`/decide/<card>` — where every nudge's **Open to decide** lands (0.13.4 S6). It does not render
`<App/>`, so it works for a visitor who is not signed in.

- **Signed out:** no card data at all (PRD M4/§16). A carried one-time link (`?t=`) shows one
  **Continue** to `/api/auth/redeem?t=…&next=/decide/<card>` — the sign-in is spent by that click,
  never by a mail scanner. Otherwise an email field posts `/api/auth/request-link` with the same `next`.
- **Signed in:** the card alone (`card/read`), every field before the decision row, then
  `card/seen` once (suppresses the pending email). A decision is `card/update` with the card's
  revision; Done reads `card/list {mine: true}` for "N more" and **Next**.
- `decideState.ts` is the pure half (tolerates missing `actions`, `kind`, `fields`, `next_actor`,
  `from`, `workspace_name`); tested in `../__tests__/decide.test.tsx`.
