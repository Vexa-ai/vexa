---
name: 3-develop
description: "Invoke during the development phase of a Vexa release — when the user is actively editing code, adding tests, wiring DoDs to frontmatter, or debugging locally. Runs in parallel with stage 2 (release-provision). Use when the user says things like 'let me code this up', 'add a test for X', 'where do I put the DoD', 'bind this test to a feature', 'update the frontmatter', 'what's the test step naming convention', or any local dev question DURING an active release cycle."
---

## Stage 3 of 9 — develop

Runs in parallel with stage 2 (release-provision). Local only — no infra touched. Your job: make the `proves:` bindings in the scope go fail→pass.

## Workflow

1. **Pick an issue** from `$SCOPE`. Read its `problem`, `hypothesis`, and `proves:` list.
2. **Find or add the test step** that the `proves:` entries reference.
3. **Write the fix** in the appropriate service(s).
4. **Run the unit tests** locally (fast feedback).
5. **Commit to `dev`.** Add the SHA to the issue's `fix_commits`.
6. **Push to origin** when ready — stages 4+ pull from `origin/dev` on the VMs.

## Where things live

### Adding a test step (used in `proves: {test, step, modes}`)

Test scripts under `tests3/tests/*.sh` use three helpers from `tests3/lib/common.sh`:

```bash
source "$(dirname "$0")/../lib/common.sh"

test_begin my-test                    # starts JSON capture; EXIT trap writes .state/reports/<mode>/my-test.json
step_pass my-step-id "message"        # emits ok to stdout AND JSON entry
step_fail my-step-id "reason"
step_skip my-step-id "reason"
# test_end is implicit via the EXIT trap
```

**Step IDs are the stable contract** — the scope's `proves:` references them. Don't rename without updating every scope that references the old ID.

Then register the test + its steps in `tests3/test-registry.yaml`:

```yaml
tests:
  my-test:
    tier: cheap         # cheap | meeting | human
    runs_in: [lite, compose, helm]
    script: tests/my-test.sh
    features: [my-feature]
    steps:
      - my-step-id
      - another-step
```

### Adding a check (used in `proves: {check, modes}`)

Checks live in `tests3/checks/registry.json` and are evaluated by `tests3/checks/run`. There are four tiers:

- `static` — grep source code (no infra needed).
- `env` — compare env vars across services (requires svc_exec; skips gracefully on helm without kubectl).
- `health` — hit service endpoints or K8s resources.
- `contract` — exercise API behavior.

Each check has an id (UPPERCASE_WITH_UNDERSCORES), `proves`, `symptom`, and check-specific fields.

### Binding DoDs to features

Each `features/*/README.md` has a YAML frontmatter with a `tests3.dods:` list. DoDs are **behavioral assertions**, mirroring the feature's "Expected Behavior" section, bound to test step(s) or check(s).

```yaml
tests3:
  gate:
    confidence_min: 95        # aggregator blocks release if feature drops below this
  dods:
    - id: my-behavior
      label: "User-visible behavior one sentence"
      weight: 10
      evidence: {test: my-test, step: my-step-id, modes: [compose, helm]}
```

The aggregator (`tests3/lib/aggregate.py`) rewrites the DoD markdown table between `<!-- BEGIN AUTO-DOD -->` and `<!-- END AUTO-DOD -->` markers. Re-runs are idempotent.

## Unit tests

Run before committing — catches regressions without needing VMs:

```bash
cd services/meeting-api && python -m pytest tests/ -v
cd services/admin-api   && python -m pytest tests/ -v
cd services/api-gateway && python -m pytest tests/ -v
```

## Commit hygiene

- **One issue per commit** where possible. Reference the issue id in the subject.
- After committing, add the SHA to the scope's `fix_commits` list so stage 6 can cite it.

```bash
git add <changes>
git commit -m "fix(<issue-id>): <one-line description>"
git rev-parse --short HEAD
# → add to scope.yaml → issues[].fix_commits
```

## Push when ready

Stage 4 (release-deploy) pulls from `origin/dev`:

```bash
git push origin dev
```

## Ground rules

- **No workarounds unless explicitly decided.** If a test is red, find and fix the actual cause. Silencing a check, marking it `skip` to hide a real failure, wrapping a call in `|| true`, special-casing one mode to hide a regression — all forbidden unless the user explicitly approves the workaround with a written reason in the scope's relevant `issues:` entry.
- **No fallbacks unless explicitly decided.** If the primary path is broken, fix the primary path. No "if step A fails, try step B". No "if JSON is missing, parse stdout". No "if env var X is empty, read Y". Every fallback grows unbounded and masks failure modes. Add one only when the user explicitly approves it AND the primary-path failure is expected (network flake, optional feature absent).
- **No stdout parsing in tests.** JSON artifacts from `step_pass`/`step_fail` are the only source of truth — never grep logs, never compare stdout.
- **No new DoDs without a test binding.** If a behavior can't be proved by an automated step or a scripted human-verify entry, it's not a DoD — it's a note.
- **Don't edit the scope's `proves:` mid-cycle.** Add new issues freely; changing an existing binding means retroactively moving the goalposts.

### What to do when tempted by a workaround or fallback

1. **Stop.** Write down (in a scratch note or the scope issue's body) what you were about to paper over.
2. **Surface the real cause.** Run the failing check in isolation; read the error; trace it to the misbehaving component.
3. **Propose to the user.** If the clean fix is impractical for this release, ask explicitly: "The right fix is X but it's out of scope. Should I (a) defer to a new issue, (b) add a workaround with reason Y, or (c) something else?" Get a decision in writing before touching the code.
4. **Record the decision.** If the user approves a workaround, add it to the scope's relevant `issues:` entry as `workaround: |` with the reason AND an expiration (when should we revisit?). Same for approved fallbacks.

The default answer to "should I add a fallback?" is **no**. Ask first.

## Next

Once your local unit tests pass and the fix is committed + pushed:
→ stage 4: `make release-deploy SCOPE=$SCOPE`
