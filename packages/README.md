# packages/ — vendored third-party-shaped packages (out of the pnpm workspace + gates)

Holds packages vendored wholesale from `main` as acknowledged debt (ADR-0007), the same class as
`clients/dashboard`: not in `pnpm-workspace.yaml`, carved out of the gates via `.gateignore`, kept with
their own `dist` so the vendored dashboard builds without a workspace build step.

- **`transcript-rendering/`** — `@vexaai/transcript-rendering@0.4.1` (prebuilt `dist`): the dashboard's
  live-transcript segment renderer. The dashboard depends on it via `file:../../packages/transcript-rendering`.

De-vendor these when the dashboard is de-vendored (ADR-0007 calls the dashboard phase).
