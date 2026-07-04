# DOCS-GAPS — 0.12 documentation debt checklist

Companion to [PARITY-MAIN.md](PARITY-MAIN.md) and the parity section in
[`docs/changelog.mdx`](docs/changelog.mdx). Every claim below was verified against this tree on
2026-07-04 (route tables, module code, or a live-edge probe) — not inferred from planning docs.
Check items off as pages land; delete the file when it's empty.

## 1 · Capability shipped, no corpus page (write these)

- [ ] **Terminal workbench** (`clients/terminal`) — the primary client; zero user-facing docs.
- [ ] **Webhooks user guide** — the module is built and gated (HMAC signing, SSRF guard, retry);
      `PUT`/`GET /user/webhook` are mounted at the gateway. No corpus page exists.
- [ ] **Token scoping** — `vxa_<scope>_…` mint format, multi-scope `?scopes=bot,tx` on the admin
      token mint, gateway per-route scope enforcement (`/bots`→bot|browser, `/transcripts` +
      `/meetings`→tx, `/recordings`→tx|bot). Undocumented.
- [ ] **Teams `passcode`** — the corpus never mentions it anywhere; this blocks anyone sending a
      Teams bot. High priority.
- [ ] **Vexa Lite** (`deploy/lite`) — one-container control plane (process runtime backend);
      `deployment.mdx` doesn't mention it. Gated on **Decision 1** in `changelog.mdx`.
      Also: `deploy/lite/README.md` says `make lite` from the repo root, but the root `Makefile`
      has no `lite` target — fix one or the other.
- [ ] **Helm chart** (`deploy/helm/charts/vexa`) — full k8s deployment
      (`RUNTIME_BACKEND=k8s`, bots as Pods); no corpus mention.
- [ ] **MCP service** (`core/meetings/services/mcp`) — compose service on `127.0.0.1:18010`,
      9 tools + 4 prompts; no corpus page. Gated on **Decision 2** in `changelog.mdx`.
- [ ] **`STORAGE_BACKEND`** — read by `recordings/jsonb.py` (default `minio`); `configuration.mdx`
      documents only `MINIO_*`. Port the storage-backend matrix from main's
      `recording-storage.mdx`, verifying env names against this tree.
- [ ] **Scheduling intent** — `PUT /meetings/{platform}/{native}/intent` is mounted at the
      gateway but undocumented. Note: this route (and `GET /user/webhook`, the `/agent/*`
      prefix) is **gateway surface beyond the sealed api.v1** — decide whether the next contract
      re-seal should include it.

## 2 · Corrections (docs said one thing, tree says another)

- [x] `PARITY-MAIN.md` §4 — "`DELETE /bots` unmounted / 404s" was **stale**: the stop route is
      wired (`lifecycle/stop_router.py` via `meeting_api/app.py`) and tested. Fixed in this PR;
      the corpus `api/meetings.mdx` was already correct.
- [ ] The earlier docs-plan claim that "`PATCH`/`DELETE /meetings`, `GET`/`PUT /recording-config`,
      `DELETE /recordings/{id}`, media `…/download` are sealed **and built** but undocumented" is
      half right: **sealed yes, built no.** None of them is mounted at the gateway and meeting-api
      has no handlers (live probes 404). They belong in the parity table
      (contract-sealed, not wired), not the docs plan — do NOT write pages that claim they work.

## 3 · Port-from-main list (content that is still true for 0.12)

From the main-docs convergence map (Lane F, 2026-07-04); port into the corpus, re-verifying env
names and selectors against this tree while porting:

- [ ] `meeting-ids.mdx` — ID-extraction rules per platform (fold the Teams passcode item above in).
- [ ] `errors-and-retries.mdx` — retry/idempotency guidance → merge into `api/errors.mdx`.
- [ ] `speaker-identification.mdx` — DOM-based speaker correlation mechanism + limits.
- [ ] `transcription-quality.mdx` — engine matrix, language support, hallucination filtering.
- [ ] `platforms/google-meet.mdx` — admission model + failure modes (0.12 already carries the
      typed denial/lobby-timeout distinction — document it).
- [ ] `platforms/microsoft-teams.mdx` — URL-format table + passcode extraction.
- [ ] `platforms/zoom.mdx` — Zoom **web client** path only (0.12 has no BYO OBF/ZAK tokens and no
      native SDK branch — do not port `zoom-app-setup.mdx`).
- [ ] `webhooks.mdx` + `local-webhook-development.mdx` — see the webhooks item in §1.
- [ ] `scaling.mdx` (partial) — per-bot resource sizing / one-browser-per-bot model; the Helm half
      now has a real in-tree target (§1).
- [ ] `self-hosted-management.mdx` (partial) — the per-endpoint admin reference tables
      (create user, `max_concurrent_bots`, token mint incl. `scopes`).
- [ ] `websocket.mdx` (partial) — segment-merge algorithm + keepalive detail as an appendix to
      `how-to/stream-transcript.mdx`.
