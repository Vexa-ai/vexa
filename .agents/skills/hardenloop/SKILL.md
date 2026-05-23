---
name: hardenloop
description: Run Hardenloop release hardening for a Vexa release pack or full candidate, record release blockers, scanner coverage, accepted risks, and evidence paths in .agents/releases/<version>/state.md without leaking private advisory or secret material.
---

# Hardenloop

## Purpose

Run the local Hardenloop adversarial hardening harness as a release gate. Use it
for every release pack and again for the complete staged candidate.

Default local binary:

```bash
/home/dima/dev/vexa-i-adversarial-harness/.venv/bin/hardenloop
```

If that path is unavailable, use `hardenloop` from `PATH`. If neither exists,
report the missing tool and do not invent a substitute scanner.

## Normal Command

From the Vexa repo root:

```bash
/home/dima/dev/vexa-i-adversarial-harness/.venv/bin/hardenloop release . \
  --mode oss-release \
  --cycles 1 \
  --fix none \
  --out .agents/releases/<release>/hardenloop/<pack-or-stage-id> \
  --config .agents/skills/hardenloop/references/vexa-release.toml \
  --timeout-seconds 180
```

Use a pack-specific output directory, for example:

```text
.agents/releases/0.10.6.2.1/hardenloop/pack-recording-playback
.agents/releases/0.10.6.2.1/hardenloop/stage-final
```

If `.agents/skills/hardenloop/references/vexa-release.toml` does not exist,
omit `--config` and record that default scanner configuration was used.

## Rules

- Run only on repositories the user owns or explicitly authorized.
- Default to `--fix none`; do not allow Hardenloop to edit files unless the user
  explicitly asks for a safe-fix pass.
- Treat `release-blockers.md`, `scanner-coverage.md`, `hardenloop-attestation.json`,
  and `SECURITY-ADVISORY-DRAFT.md` as evidence. Summarize them; do not paste a
  private advisory wholesale.
- If scanner coverage is incomplete because tools are missing, record it as a
  coverage caveat rather than silently passing.
- A Hardenloop decision of `blocked` blocks the pack or release until fixed or
  explicitly accepted by the human in `state.md`.
- Do not print secrets from environment files, kubeconfig, service logs, or
  generated private artifacts.

## State Update

After each run, update `.agents/releases/<release>/state.md` with:

- pack or stage id;
- command shape, with secrets redacted;
- output directory;
- Hardenloop decision, if present;
- release blockers count and names;
- scanner coverage caveats;
- accepted risks or required fixes;
- whether the pack may advance.

## Completion Criteria

The hardening gate is complete when:

- the command exits successfully or the failure is diagnosed;
- release blockers are summarized;
- scanner coverage is summarized;
- state records pass, block, or human-accepted risk.
