# app/debug/cards

The card lab: the S5 card components (`minutes/cards`) on synthetic fixtures, with `/api/desktop/card/*`,
`/api/people/mentionable` and `/api/desktop` answered by an in-browser stub (`fixtures.ts`). Used to check
rows, the sheet, the decision block, the inline card and the held Decline at desktop and phone widths.
A production build answers 404 (`gate.ts`). Calls made by the components are on `window.__cardLab.calls`;
`?theme=light` starts in day mode.
