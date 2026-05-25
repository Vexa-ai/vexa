# Pack PR: [Pack] PACK 6 - Self-Hosted Browser Edge

Pack epic: https://github.com/Vexa-ai/vexa/issues/361
Pack id: `0.10.6x-pack-6-self-hosted-browser-edge`
Release: `0.10.6.x replay`
Base branch: `v0.10.6^{}`
Integration branch: `codex/release-0.10.6x-pack-integration`
Evidence: `.agents/packs/0.10.6x-pack-6-self-hosted-browser-edge/`

## Outcomes

CEO: Self-hosted users can open the dashboard and browser views without internal container URLs or unintended host-port exposure.

CTO: Browser-facing config/proxy/auth edges are explicit, public, and separate from internal service routing across Lite, Compose, and Helm-shaped deployments.

User: The dashboard works from the browser, while Meeting API/Admin/Runtime/TTS/Redis/Postgres stay internal in self-hosted Lite.

## Scope

- #348
- browser/Lite portions of #331

## Blast radius

Dashboard runtime config, auth cookies, proxy routes, VNC/CSP, Lite network model, Helm runtime bot launch, self-hosted docs.

## Validation

Setup-only at this point: runtime allocated, Compose stack up on pack-scoped ports (dashboard 42020, gateway 42021), images re-tagged `:pack-6-dev`. Synthetic / Compose / Lite / Live gates not yet exercised — develop skill will fill these.

## PR readiness checklist

- [ ] Pack branch starts from `v0.10.6^{}`.
- [ ] Only this pack's committed reuse hunks are replayed.
- [ ] Synthetic checks pass before live/human checks.
- [ ] Compose gate is passed or explicitly marked not required in PR evidence.
- [ ] Lite gate is passed or explicitly marked not required in PR evidence.
- [ ] Hardenloop is run for the pack.
- [ ] PR body links this epic and evidence root.
- [ ] Reviewer can map each reused hunk back to the commit list above.
