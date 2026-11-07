# data

The terminal's HTTP data access, below every view: `apiClient.ts` (the one fail-loud `getJson`, the
`ApiError` it throws and `presentError`, which turns one into the sentence a person reads),
`redactSecrets.ts` (what never reaches a person or the console) and `workspaceApi.ts` (the workspace,
file, git and membership reads and writes).

They lived in `surfaces/`, and the ui-kit's document renderer reads workspaces through
`workspaceApi`, so ui-kit and surfaces imported each other. Here they sit under both. This folder
imports only `platform/`. The ui-kit still reaches `workspaceApi` by lazy `import()`, so a test that
renders a paragraph does not load the HTTP client.
