# Scope Design — v0.10.6.1

Status: express restart
Stage: `scope-design`

This is the concise, human-centered start of the release:

```text
why -> what -> how
```

The later stages expand this into implementation scaffolding, then compress it
again through machine pushback and human signoff.

## Why

v0.10.6 shipped useful fixes but left several high-impact production problems
active or newly visible. v0.10.6.1 exists to restore trust in the core customer
paths: recording playback, in-meeting speech/TTS, meeting lifecycle cleanup,
and recording finalization correctness.

This is not a broad feature release. It is a focused hotfix and
low-hanging-fruit release where the release system must prove the claims
instead of asking the human to infer them.

## What

In scope:

1. Tier-1 production regressions:
   - multi-chunk playback truncated to first 30 seconds;
   - `/speak` / TTS outage and provider handling;
   - `browser_session` DELETE stuck in `stopping`;
   - `post_meeting` / `recording_finalizer` master race.
2. Community / PR merge sweep selected by maintainer signal.
3. Durable fixes:
   - vexa-lite docs/env hygiene;
   - WebM duration tag follow-up;
   - GMeet rejection / waiting-room fast-fail;
   - arm64 / Apple Silicon image support.
4. Hygiene:
   - broad-except narrowing;
   - chunk-write prior-count log fix;
   - stale issue audit sweep.

Out of scope:

- [#289 dashboard/api-gateway 429](https://github.com/Vexa-ai/vexa/issues/289)
  — removed from this cycle because the issue's "dashboard never populates"
  framing no longer matches current production behavior; re-triage against
  fresh prod logs before pulling it into any release.
- [#303 audit-stage wiring](https://github.com/Vexa-ai/vexa/issues/303)
  — useful release-system work, but not part of this hotfix; it needs a
  dedicated cycle to wire the Make target, stage transitions, audit skill, and
  static audit patterns.
- Bot broadcast-surface regression class.
- K8s container-id orphan-bot.
- Long-recording transcribe pipeline.
- Bot lifecycle classifier hardening.
- Zoom reliability.
- Discord fetcher in-repo.

## How

Design stance:

- Keep the release bounded around production-impacting regressions and small
  durable hygiene.
- Prefer root-cause fixes over symptom patches.
- Do not introduce silent fallbacks unless explicitly decided and documented.
- Use registry checks for machine-verifiable claims.
- Reserve human validation for product judgment and signoff.
- Treat stage/helm as production-equivalent for release confidence where the
  claim is helm-bound.
- Keep public release and production rollout distinct.

## Human Decisions

Accepted:

- Make v0.10.6.1 a focused hotfix and low-hanging-fruit release.
- Include the selected Tier-1 production regressions.
- Include selected low-risk durable fixes and hygiene.
- Remove [#289](https://github.com/Vexa-ai/vexa/issues/289) from this cycle
  until it is re-triaged against current production behavior.

Open:

- Whether any selected community PR should defer after code review.
- Whether any helm-only proof gap blocks release or becomes an explicit
  deferral.
- Whether `release-sign` should emit a standalone production handoff YAML.

## Exit

`scope-design` is complete when the human can answer:

- Why does this release exist?
- What is in and out?
- What design stance constrains implementation?
- What must be expanded by `scope-deliver`?
