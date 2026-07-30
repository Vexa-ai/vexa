# architecture — how the runtime works (Mintlify pages)

The mechanism behind the primitives: `dispatch.mdx` (trigger → unit.v1 → the one Dispatcher),
`execution.mdx` (worker spawn + workspace mount via runtime.v1), `governance.mdx` (the claude-turn
guardrails), `streaming.mdx` (SSE output), `identity-and-trust.mdx` (the trust chain).

`react-engineering-standard.mdx` is the canonical React 19.2 author/reviewer standard for
`clients/terminal`. It is subordinate to the architecture constitution and does not redefine
the repository-wide governance contract.
