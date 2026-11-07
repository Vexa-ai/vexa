# blocks.v1 — the shared block vocabulary — UNSEALED

One vocabulary for three surfaces: the new-chat home (`home-card.v1` is its envelope), workspace
pages and agent chat replies. In a page or a reply the blocks ride in a fence, so the file stays plain
markdown in git and to an agent:

````markdown
```vexa
{"v":1,"people":{"mira":{"name":"Mira Chen","vexa":true}},"blocks":[{"type":"title","text":"Open items"}]}
```
````

Primitives `title`, `text`, `person`, `row`, `list`, `evidence`, `checklist`, `action`; layouts
`group` (collapsible, with a summary) and `attach` (a card in a card). Plain text only. Actions send a
prompt, open a chat or page by id, message a person, or expand that spot in place. Consumers drop what
they do not know and cap text at `x-limits`; nesting stops at depth 3. Readers: agent-api
`control_plane/home.py` (`sanitize_view`, `sanitize_fence`) and the terminal's
`ui-kit/blocks/spec.ts`; one renderer, `ui-kit/blocks/Blocks.tsx` (`BlocksView`, `CardView`,
`VexaFence`, `Peek`, `PersonChip`).
