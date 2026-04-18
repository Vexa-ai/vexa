---
name: 1-plan
description: "Invoke at the START of a Vexa release cycle — when the user wants to plan a release, open a new release cycle, scope the work, pick which GitHub issues to address, write problem → hypothesis → fix bindings, or decide which deployments to validate against. Produces tests3/releases/<id>/scope.yaml: the contract that drives every subsequent stage. Use when the user says things like 'start a release', 'plan the next release', 'scope this', 'new release cycle', 'let's batch these issues', 'open scope.yaml', 'what are we shipping'."
---

## Stage 1 of 9 — plan

You are setting up the scope file that every later stage reads. Nothing else runs until this is done.

## Command

```bash
cd ~/dev/vexa
make release-plan ID=<slug>               # e.g. ID=260417-webhooks-dbpool
$EDITOR tests3/releases/<ID>/scope.yaml
export SCOPE=tests3/releases/<ID>/scope.yaml
```

`make release-plan` copies `tests3/releases/_template/scope.yaml` into the new directory and substitutes the release ID. The rest is your job.

## What to fill in

For **every issue** in flight this release, write one `issues:` entry:

- `id`: short slug (e.g. `webhook-gateway-injection`).
- `source`: `gh-issue` | `human` | `internal` | `regression`.
- `ref`: optional GH issue ID/URL or human-report ref.
- `problem`: user-observable symptom, **from the user's point of view**. What breaks?
- `hypothesis`: root-cause theory. What do we believe is actually happening?
- `fix_commits`: list of git SHAs. Empty at start; grow as you commit.
- `proves`: list of `{test, step, modes}` or `{check, modes}` bindings — the specific JSON artifacts that must go fail→pass once the fix is correct.
- `required_modes`: deployments that MUST go green for this issue before ship. Subset of `deployments.modes`.
- `human_verify`: list of `{mode, do, expect}` — steps for the human checklist at stage 6b.

## Sourcing issues

```bash
gh issue list --repo Vexa-ai/vexa --state open --limit 20
```

Plus any human reports ("user says webhooks don't fire after stop"). Trace connections — if two issues share a root cause, merge them into one `issues:` entry with two symptoms.

## Deployments

```yaml
deployments:
  modes: [lite, compose, helm]
```

**If you're unsure whether a mode is affected, include it.** Better to run extra tests than miss a platform-specific regression.

## Gate overrides

```yaml
strict_features:
  - webhooks
  - infrastructure
```

Any feature listed here requires **100% confidence** for this release (overrides its README frontmatter's `gate.confidence_min`). Use sparingly for features directly touched by the release.

## Verify the scope before moving on

```bash
python3 -c "import yaml; s=yaml.safe_load(open('$SCOPE')); [print(i['id']) for i in s['issues']]"
```

Every issue should have a non-empty `proves:` list. If you can't name a test step that proves a fix, the hypothesis isn't testable yet — refine it before proceeding.

## Next

Once the scope is written:
- Kick off stage 2 (`make release-provision SCOPE=$SCOPE`) in a background shell.
- In parallel, begin stage 3 (develop code + tests). Do not wait for provision to finish.

## Key files

- `tests3/releases/_template/scope.yaml` — the schema (every field documented inline)
- `tests3/test-registry.yaml` — catalog of tests you can reference in `proves:`
- `tests3/checks/registry.json` — catalog of check IDs you can reference in `proves:`
- `tests3/README.md` — the full flow doc
