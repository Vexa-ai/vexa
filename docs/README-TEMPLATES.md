# README Template

> Every README in this repo follows this template. Procs enforce it —
> after every run, the owning proc verifies the README matches reality
> and fixes drift. A README that doesn't follow the template is a bug.

```markdown
# Name

## Why
One paragraph. Who needs this, what problem it solves.

## What
How it works. Architecture, data flow, endpoints, state machines.

### Components
| Component | File | Role |
|-----------|------|------|

### API / Configuration / Steps
(whatever tables are relevant — endpoints, env vars, ports, instructions)

## How
How to use it. Exact commands, curl examples, step-by-step.

## DoD
| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|

Confidence: N

## Known Issues
| # | Issue | Impact | Status |
|---|-------|--------|--------|
```

## What procs check after each run

1. All sections exist (Why, What, How, DoD)
2. Tables match reality (file paths exist, endpoints respond, env vars match)
3. DoD updated (Status, Evidence, Last checked, Confidence recalculated)
4. No stale claims — if reality changed, the doc changed too
5. Missing section = FIX. Stale claim = FIX. Wrong table = FIX.
