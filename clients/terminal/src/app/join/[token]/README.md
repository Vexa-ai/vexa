# terminal/src/app/join/[token]

`GET /join/<token>` — the spelling a join link is handed out in (a calendar event body, an email;
#1783). It redirects to `/join?i=<token>`, the page that previews and redeems the link, so there is
one redeemer. `joinPathFor` in `../joinState.ts` does the mapping; tested in `../../__tests__/join.test.tsx`.
