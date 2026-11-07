- **The desktop on the home (#1783, behind the `desktop` feature, off by default).** With it on, the
  home keeps its greeting and composer and shows the person's desktop below them in place of the
  composed cards: their workspaces (kind and access, last activity, last call, a live call with Join,
  members with a presence dot only for "now" or "today"), the agents and files they added with "Add to
  desktop" from a routine's or a file's "…", in the order they arranged. Sections and tiles are
  reordered by drag, collapsed, hidden and shown again; each change can be undone, the same changes
  their agent can make when asked. What an agent proposes waits as "Vexa suggests … · Apply · Not now",
  and an active filter shows as a chip that clears it. `GET /api/desktop` composes this per request from
  the person's arrangement (crm) and the tiles (agent) and stores nothing; with the feature off, or the
  desk refused, the home is unchanged.
