# app/w/[...ref]

The catch-all segment behind `/w/<workspace-id-or-slug>/<path>`: one route for a workspace root and
for any file inside it, because a document path has an unbounded number of segments and a fixed
route would only address the depth somebody guessed.

The page renders `<App />` — the same shell every other route renders — **and nothing else**. The
shell reads the address bar itself (`minutes/MinutesShell.tsx`), because honouring the link is one
move with choosing the chat it opens in and with saying one sentence when it cannot open. This page
used to resolve the id and dispatch into the panel on a timer, which made it a second writer of the
panel racing the shell's own first layout — Vexa-ai/vexa#1643.

Segment safety is enforced in `../../workspaceRoute.ts`: a `..` never survives parsing, at either
end. A link is text somebody wrote into a document, so the URL built from one is untrusted input.
