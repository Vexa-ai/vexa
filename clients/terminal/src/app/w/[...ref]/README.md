# app/w/[...ref]

The catch-all segment behind `/w/<workspace-id>/<path>`: one route for a workspace root and for any
file inside it, because a document path has an unbounded number of segments and a fixed route
would only address the depth somebody guessed.

The page renders `<App />` — the same shell every other route renders — and dispatches the shell's
own open-entity event once the id resolves. Going through that event rather than a prop is what
keeps this route to one file: the shell already knows how to put a `{path, slug}` in the panel, and
a second way in would be a second thing to keep in step.

Segment safety is enforced in `../../workspaceRoute.ts`: a `..` never survives parsing, at either
end. A link is text somebody wrote into a document, so the URL built from one is untrusted input.
