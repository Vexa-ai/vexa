# join/src/telemost — Yandex Telemost join flow

Joins the canonical web client at `https://telemost.yandex.ru/j/<10-digit-id>` as a
guest. The adapter keeps platform-owned DOM knowledge here: browser continuation,
guest-name entry, receive-only prejoin controls, admission, removal, and leave.
Recording and transcription remain host concerns outside `@vexa/join`.

Selectors intentionally combine stable attributes with Russian and English accessible
labels. The live UI is the final oracle; `join.test.ts` pins URL validation offline.
