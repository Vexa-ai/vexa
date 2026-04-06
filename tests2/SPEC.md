# Procs — Agent Procedure Language

> **Why:** Prompts have no structure — agents skip steps, invent commands, declare success without evidence. Software has no flexibility — it can't diagnose, interpret, or adapt. This is the gap. Deterministic sequencing, non-deterministic interpretation. A warehouse worker with a manual: the manual controls what and when, the worker handles how.
> **What:** A grammar for structured-English programs that agents execute.
> **How:** Read this file. Then run any `cookbooks/*.md`. Cookbooks call `src/`, which calls `lib/`. Same syntax everywhere.

## File anatomy

Every file is a function with a typed signature. `needs:` and `gives:` are the contract — the agent knows what's required before starting and what must exist when done. Without this, agents silently proceed with missing inputs and produce undefined outputs.

```
---
needs: [INPUT1, INPUT2]        # contract: what must exist before this runs
gives: [OUTPUT1, OUTPUT2]      # contract: what will exist after this runs
---

use: lib/http                  # imports: which functions are available
use: lib/docker

# Title

> **Why:** reason this exists — without it, the agent follows steps blindly.
> **What:** what this file does in one sentence.
> **How:** how it works — which modules it calls, what strategy it uses.

## state

    VAR = value                # workspace: mutable, scoped to this file

## steps

    1. step_name               # the program: sequential by default
       ...
```

`needs:` — stop if missing. No guessing, no defaults.
`gives:` — must exist after execution. If absent, the procedure failed.
`use:` — import a module. Its `fn` blocks become callable.
`state:` — mutable workspace. Read with `{VAR}`, write with `=>`.

## Steps

```
N. step_name [modifiers]
   > Why this step exists.

   do: shell command
   expect: success condition
   => VAR = captured value
   on_fail: stop | continue | retry(max=N, wait=Ns) | ask | fix
```

`do:` — exact shell command. Do not modify it.
`call:` — invoke an imported `fn` or another `src/` module.
`ask:` — present message to human, wait for response.
`expect:` — assertion. If false, step fails.
`=>` — capture into state.
`on_fail:` — what to do when expect fails.

A step must have exactly one of: `do:`, `call:`, or `ask:`.

Modifiers: `[human]` requires human, `[optional]` failure doesn't block, `[idempotent]` safe to retry.

## Control flow

```
for VAR in [a, b, c]:
    1. ...
```
Iterate. Each iteration gets own state scope inheriting parent.

```
if CONDITION:
    1. ...
else:
    1. ...
```
Branch. Exactly one branch executes.

```
repeat until CONDITION (max N):
    1. ...
```
Convergence loop. `RUN_NUMBER` auto-increments (1-indexed).

```
parallel:
    branch NAME:
        1. ...
    branch NAME:
        1. ...
    join: wait for all
```
Opt-in only. Default is sequential. Branches share read-only parent state.

## Calling modules

```
call: src/infra
=> GATEWAY_URL, ADMIN_TOKEN
```
Runs the module's steps. Its `gives:` outputs become available via `=>`.

```
call: src/bot(MEETING_PLATFORM="google_meet", MEETING_URL={URL})
=> RECORDER_ID
```
Pass arguments that satisfy the module's `needs:`.

```
call: lib/http.check_url(URL={GATEWAY_URL})
=> OK, STATUS_CODE
```
Call a library function.

## Module system

### Library (`lib/*.md`)

Define `fn` blocks. Pure functions — no side effects on parent state.

```
fn check_url(URL, EXPECTED=200):
    do: curl -sf -o /dev/null -w "%{http_code}" "{URL}"
    expect: output == {EXPECTED}
    => OK = true/false
    => STATUS_CODE = output
```

### Procs (`src/*.md`)

Full procedures with `needs:`, `gives:`, `state:`, `steps:`.
Don't import each other. Shared logic goes in `lib/`.

### Cookbooks (`cookbooks/*.md`)

Entry points. Compose procs via `call:`. Each cookbook
is itself a proc — same syntax, different selection of modules.

### Config (`env.md`)

Flat state block. Imported by cookbooks via `use: env`.
Swap for different environments.

## Events

Every step emits exactly one:

| Event | Meaning |
|---|---|
| `START` | Beginning step or procedure |
| `PASS` | Succeeded with evidence |
| `FAIL` | Software is broken |
| `FIX` | Agent corrected a doc/script |
| `FINDING` | Unexpected observation |
| `SKIP` | Skipped with reason |

Format: `[ISO8601] EVENT module/step: message`

### Logging

Events are persisted via `lib/log`. Every cookbook must:

1. `use: lib/log`
2. Call `log.init(COOKBOOK={name})` before any steps
3. Use `log.emit(...)` for every event (or `log.gap(...)` for failures)
4. Call `log.summary(...)` and `log.close()` at the end

Logs go to `tests2/runs/`:
- `{cookbook}-run{N}-{YYMMDD-HHMM}.log` — human-readable
- `{cookbook}-run{N}-{YYMMDD-HHMM}.jsonl` — machine-parseable
- `{cookbook}-latest.log` — symlink to most recent

Logs are gitignored artifacts. They survive locally but are not committed.

## Self-heal (`on_fail: fix`)

```
try:
    do: {command}
    expect: {condition}
fix:
    classify: doc_gap | script_bug | software_bug

    if doc_gap or script_bug:
        propose 1-3 fixes
        confirm: "Apply fix N?"
        if confirmed: apply, emit FIX, retry
    if software_bug:
        emit FAIL, do not fix application code
```

Agent fixes docs and scripts only. Never application code.
Every fix requires human approval.

## Execution rules

1. **Sequential by default.** N+1 waits for N.
2. **No skipping.** Every step runs unless `[optional]` or excluded by `if:`.
3. **No improvising.** `do:` commands run exactly as written.
4. **State is explicit.** Only `needs:`, `state:`, and `=>` values exist.
5. **Every step emits.** No event = did not execute.
6. **Failures propagate.** `on_fail:` is the procedure's decision, not the agent's.
7. **Human approval for fixes.** Never apply a fix without `confirm:` or `ask:`.
