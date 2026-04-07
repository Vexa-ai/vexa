# CLAUDE.md

---
needs: [TASK, MODE]
gives: [TASK_RESOLVED, STATE_CLEAN]
---

use: tests2/SPEC
use: tests2/GRAPH
use: tests2/INVENTORY
use: tests2/env

# Agent Operating Procedure

> **Why:** Without structure, agents skip steps, invent commands, declare success without evidence, and leave dirty state behind. This procedure prevents that.
> **What:** The top-level loop for every task — bug, feature, validation, debug.
> **How:** Read the proc system, pick a cookbook, validate state, execute, fix forward, leave clean.

## Project

Vexa — self-hostable meeting transcription API. Bots join Google Meet / Teams, transcribe real-time, agent runtime for post-meeting automation.

Build & run: `cd deploy/compose && make build && make up`

## Coding rules

- No fallbacks. If something fails, it fails. Do not add fallback logic, default values, silent catches, or alternative paths unless the human explicitly decides to add one. A failure must be visible, not hidden behind a workaround.

## modes

Two modes. Never mix them. The mode determines what you touch and what you don't.

### author

> You are writing the exam, not taking it.

Use when: creating procs, updating procs, writing cookbooks, defining DoD criteria, hardening the proc system itself.

**You MUST:**
- Read the relevant feature README and its DoD table
- Ensure every DoD criterion has a proc step that validates it
- Ensure every proc has needs/gives, steps with do:/expect:, on_fail:, and a Failure modes table
- Validate structure: does the proc fit in GRAPH.md? Are needs/gives consistent with INVENTORY.md?

**You MUST NOT:**
- Run `do:` commands against the live system
- Declare a proc "works" — that is execute mode's job
- Skip writing Failure modes tables ("I'll add them later" = never)

**Output:** new or updated `.md` files in `tests2/src/`, `tests2/lib/`, `tests2/cookbooks/`, `features/*/README.md`

### execute

> You are taking the exam, not writing it.

Use when: running a cookbook, debugging, validating, reproducing a bug.

**You MUST:**
- Follow procs exactly as written — `do:` commands run verbatim
- Emit events, log to `tests2/runs/`, check every `expect:`
- Report failures with evidence (output, status code, error)

**You MUST NOT:**
- Edit proc steps, restructure flows, or rewrite commands while executing
- Add features or "improvements" to procs mid-run
- Improvise commands not in the proc

**Exception — step 8 (update_proc):** After fix/rerun cycle completes, you enter a controlled author moment:
- Add failures to the Failure modes table
- Fix the specific wrong command or expect that you discovered
- That's it. No restructuring. No new features. Patch what broke.

**Output:** test results in `tests2/runs/`, minimal proc patches

### Mode detection

If TASK is not explicitly tagged with a mode:

```
if TASK mentions "create proc", "write test", "add DoD", "harden", "new cookbook":
    => MODE = author
if TASK mentions "run", "debug", "validate", "reproduce", "check", "test":
    => MODE = execute
if ambiguous:
    ask: "Author mode (create/update procs) or execute mode (run procs against the system)?"
```

### Mode transition

Never drift. If you are in execute mode and realize the proc needs more than a Failure modes patch:
```
emit FINDING "proc {name} needs restructuring — requires author mode"
ask: "Switch to author mode to update this proc?"
```

If you are in author mode and want to verify what you wrote:
```
emit FINDING "proc {name} ready for validation — requires execute mode"
ask: "Switch to execute mode to run this proc?"
```

## state

    MODE          = ""
    COOKBOOK       = ""
    STATE_VALID   = false
    STATE_CLEAN   = false
    TASK_RESOLVED = false

## state_management

Every state variable has a tier, a validator, and a producer. The tier determines lifetime. The validator is a concrete command that proves the value is live. The producer is the proc that creates it.

### Tiers

| Tier | Lifetime | Rule |
|---|---|---|
| static | forever | Values from `env.md`. Always valid. Never probe. |
| infra | until service restart | Valid while containers run. Probe with health check. |
| credential | until revoked | Valid while authenticated request succeeds. |
| session | until container stops | Valid while the container is running and responsive. |
| ephemeral | until meeting ends | Valid only during an active meeting. Assume missing between sessions. |
| result | never cached | Test outputs. Always produce fresh. Never skip. |

### State table

| Variable | Tier | Validator | Producer |
|---|---|---|---|
| DEPLOY_MODE | infra | `docker.detect_mode()` returns compose\|lite\|helm | `src/infra` |
| GATEWAY_URL | infra | `curl -sf -o /dev/null $GATEWAY_URL/` exits 0 | `src/infra` |
| ADMIN_URL | infra | `curl -sf $ADMIN_URL/admin/users -H "X-Admin-API-Key: $ADMIN_TOKEN"` exits 0 | `src/infra` |
| ADMIN_TOKEN | credential | validated with ADMIN_URL probe | `src/infra` |
| DASHBOARD_URL | infra | `curl -sf -o /dev/null $DASHBOARD_URL/` exits 0 | `src/infra` |
| USER_ID | credential | validated with API_TOKEN probe | `src/api` |
| API_TOKEN | credential | `curl -sf $GATEWAY_URL/bots/status -H "X-API-Key: $API_TOKEN"` returns 200 | `src/api` |
| SESSION_TOKEN | session | `curl -sf $CDP_URL/json/version` returns JSON | `src/browser` |
| CDP_URL | session | validated with SESSION_TOKEN probe | `src/browser` |
| MEETING_URL | ephemeral | `GET /meetings?native_meeting_id=$ID` returns data | `src/meeting` |
| NATIVE_MEETING_ID | ephemeral | validated with MEETING_URL probe | `src/meeting` |
| RECORDER_ID | ephemeral | `GET /bots/status` shows bot with this ID active | `src/bot` |
| BOT_ADMITTED | ephemeral | bot status == "active" (not "awaiting_admission") | `src/admit` |
| *_OK, WER, SEGMENTS | result | — | never skip |

### Lifecycle

```
                    ┌──────────────────────────────┐
                    │                              │
unknown ──probe──→ valid ──use──→ skip producer    │
     │               │                             │
     │               └── service restart/timeout ──┘
     │                         ↓
     └──────────→ missing ──run producer──→ valid
```

### Probe order

Probe in dependency order. Stop at the first tier that fails — everything downstream is invalid.

```
1. static     — env.md values, no probe needed
2. infra      — DEPLOY_MODE, GATEWAY_URL, ADMIN_URL, DASHBOARD_URL
3. credential — ADMIN_TOKEN, API_TOKEN
4. session    — SESSION_TOKEN, CDP_URL
5. ephemeral  — MEETING_URL, RECORDER_ID, BOT_ADMITTED
6. result     — never probe, always produce
```

If tier 2 (infra) fails, skip probing tiers 3-5 — run `src/infra` first.
If tier 3 (credential) fails, skip probing tiers 4-5 — run `src/api` first.

## steps

```
1. load_grammar
   > You cannot execute what you have not read.
   do: read tests2/SPEC.md
   expect: understand needs/gives, steps, events, do/call/ask, on_fail, self-heal
   on_fail: stop

2. determine_mode
   > Detect from TASK or ask. Never proceed without a mode.
   > Write mode to state file — the Stop hook reads it.
   if TASK mentions "create proc", "write test", "add DoD", "harden", "new cookbook", "update proc":
       => MODE = author
   if TASK mentions "run", "debug", "validate", "reproduce", "check", "test":
       => MODE = execute
   if ambiguous:
       ask: "Author mode (create/update procs) or execute mode (run procs against the system)?"
   do: echo '{"mode":"{MODE}","proc":"none","confidence":0,"target":90}' > tests2/runs/.proc-state
   on_fail: stop

3. understand_task
   > A bug, a feature, a validation request. Understand what needs to be true.
   expect: TASK is concrete — not "test everything" but "bot fails to stop" or "validate deploy docs"
   if TASK is vague:
       ask: "What specifically needs to be true that isn't?"
   on_fail: stop
```

### if MODE == author

> Observe. Write DoDs. Update procs. Run them. Debug them until they pass.
> The proc is under test, not the system.

```
4a. observe
    > What happened? What's broken? What should be true that isn't?
    > Turn observations into concrete, testable DoD criteria.
    > "stale API key causes silent 401s" → Check: "API calls with invalid token return visible error, not silent empty response"
    > "token invalidated on restart" → Check: "Dashboard works after service restart without manual token refresh"

    do: read features/*/README.md — find or create the feature README that owns this area
    for ITEM in TASK (observed bugs, requirements, acceptance criteria):
        do: append to feature's DoD table:
            Check: concrete assertion (what must be true)
            Weight: 5-20
            Ceiling: true if failure = instant zero
            Status: UNTESTED
    on_fail: stop

5a. assign_proc
    > Each criterion must be owned by exactly one proc.
    do: read tests2/GRAPH.md, tests2/INVENTORY.md

    for CRITERION in new DoD rows:
        if existing proc's domain covers this:
            => Tests = existing proc name
        else:
            do: create tests2/src/{name}.md — needs/gives only, empty steps, empty Failure modes
            do: update tests2/INVENTORY.md and tests2/GRAPH.md
            => Tests = new proc name

    do: update DoD table Tests column
    do: write ownership footer to feature README
    if new proc not reachable from any cookbook:
        do: create or update tests2/cookbooks/{name}.md

    => PROCS = all procs that own new DoD rows
    => DODS = all new DoD rows with weights
    => TARGET = 90
    => CONFIDENCE = 0
    do: echo '{"mode":"author","proc":"{PROCS}","confidence":0,"target":90,"hardened":false}' > tests2/runs/.proc-state
    on_fail: ask

6a. update_procs
    > Write or update proc steps to cover the new DoD criteria.
    > The proc needs steps that can validate each criterion.
    for PROC in PROCS:
        do: read tests2/src/{PROC}.md
        for CRITERION owned by this proc:
            if no step validates this criterion:
                do: add step with DoD reference — write your best guess for do:/expect:
        do: ensure Failure modes table exists (even if empty)
    on_fail: ask

7a. run_and_debug
    > Run the procs against reality. They will fail. That's the point.
    > Debug the PROC, not just the system. Fix the proc until it passes.

    call: log.init(COOKBOOK="author-hardening")

    repeat until CONFIDENCE >= TARGET (max 10):

        emit FINDING "run {RUN_NUMBER}: confidence={CONFIDENCE}/{TARGET}"

        7a-1. follow_proc
            for STEP in proc:
                do: run the step
                if PASS: emit PASS with evidence
                if FAIL: => log failure, continue to next step

        7a-2. fix_proc
            > Fix the PROC to match reality. Not the other way around.
            for FAILURE in this run's failures:
                classify: proc_wrong | software_bug | infra_issue

                if proc_wrong:
                    > The proc's do: or expect: doesn't match how the system actually works.
                    do: fix the step — write the command that actually works, the condition that's actually true
                    emit FIX "proc step fixed"

                if software_bug:
                    emit FAIL with evidence
                    ask: "Software bug found. Fix application code?"

                if infra_issue:
                    do: diagnose
                    ask: "Infra issue: {diagnosis}. How to proceed?"

            > Record failure modes from this run.
            for FAILURE in this run's failures:
                do: append to proc's Failure modes table:
                    symptom | cause | fix | learned

        7a-3. score
            for CRITERION in DODS:
                if validated this run: Status = PASS
                else: Status = FAIL
            => CONFIDENCE = sum(DOD.Weight for DOD in DODS if DOD.Status == PASS)
            do: echo '{"mode":"author","proc":"{PROCS}","confidence":{CONFIDENCE},"target":{TARGET},"hardened":false}' > tests2/runs/.proc-state
            emit FINDING "score: {CONFIDENCE}/{TARGET}"

    on_fail: ask

8a. finalize
    > Procs pass. Write evidence back to DoD tables. Mark hardened.
    for CRITERION in DODS:
        do: update feature README DoD row:
            Status: PASS
            Evidence: actual output from the passing run
            Last checked: now
            Tests: proc name

    do: echo '{"mode":"author","proc":"{PROCS}","confidence":{CONFIDENCE},"target":{TARGET},"hardened":true}' > tests2/runs/.proc-state
    call: log.summary(...)
    call: log.close()
    => TASK_RESOLVED = true
```

### if MODE == execute

> You are following established procs. You don't stop until they pass.

```
4e. scope
    > Know your procs, know your DoDs. This defines what you're responsible for
    > and when you're done.

    do: read tests2/GRAPH.md, tests2/INVENTORY.md
    do: ls tests2/cookbooks/*.md

    > Identify all procs assigned to this task's DoD criteria.
    do: read the feature README(s) referenced by TASK
    => PROCS = list of procs in Tests column of DoD table
    => DODS = all DoD rows assigned to those procs (with weights)
    => TARGET = 90 (sum of passing DoD weights must reach this)
    => CONFIDENCE = 0

    > Find or compose the cookbook that reaches all target procs.
    if existing cookbook covers all PROCS:
        => COOKBOOK = matching cookbook
    else:
        compose chain from tests2/src/*.md covering all PROCS
        => COOKBOOK = composed chain

    emit FINDING "scope: {len(PROCS)} procs, {len(DODS)} DoDs, target={TARGET}"
    on_fail: ask

5e. assess_state
    > Probe state by tier, in dependency order. Stop at the first tier that fails.
    > See state_management section above for the full table.

    5e-1. probe_infra
        do: docker.detect_mode()
        => DEPLOY_MODE or missing
        if DEPLOY_MODE exists:
            do: curl -sf -o /dev/null {GATEWAY_URL}/
            do: curl -sf {ADMIN_URL}/admin/users -H "X-Admin-API-Key: {ADMIN_TOKEN}"
            do: curl -sf -o /dev/null {DASHBOARD_URL}/
        if any fail:
            => all tiers below are invalid — run from src/infra
            emit FINDING "infra tier invalid — will run from src/infra"
            skip to step 6e

    5e-2. probe_credentials
        do: curl -sf {GATEWAY_URL}/bots/status -H "X-API-Key: {API_TOKEN}"
        if fails:
            => credential tier invalid — run from src/api
            emit FINDING "credential tier invalid — will run from src/api"
            skip to step 6e

    5e-3. probe_sessions
        if COOKBOOK needs SESSION_TOKEN:
            do: curl -sf {CDP_URL}/json/version
            if fails:
                => session tier invalid — run from src/browser
                emit FINDING "session tier invalid — will run from src/browser"

    5e-4. probe_ephemeral
        if COOKBOOK needs MEETING_URL or RECORDER_ID:
            > Ephemeral state rarely survives between sessions. Probe but expect missing.
            do: curl -sf {GATEWAY_URL}/meetings?native_meeting_id={NATIVE_MEETING_ID} -H "X-API-Key: {API_TOKEN}"
            if fails:
                => ephemeral tier missing — will create fresh meeting

    5e-5. check_dirty
        do: docker ps --filter "name=vexa-recorder" --format '{{.Names}}'
        do: docker ps --filter "name=vexa-browser" --format '{{.Names}}'
        if orphan containers found:
            ask: "Found leftover containers: {list}. Clean up before proceeding?"
            if yes: stop and remove them

    => STATE_VALID = highest valid tier reached
    => STATE_CLEAN = no orphans, no stale sessions
    emit FINDING "state: valid through {highest_valid_tier}, starting from {first_required_proc}"
    on_fail: stop

6e. run_loop
    > Run until CONFIDENCE >= TARGET. Do not stop on individual failures.
    > Each iteration: follow known path, log failures, fix, update proc, score.

    call: log.init(COOKBOOK={COOKBOOK})

    repeat until CONFIDENCE >= TARGET (max 10):

        emit FINDING "run {RUN_NUMBER}: confidence={CONFIDENCE}/{TARGET}"

        6e-1. follow_known_path
            > Steps with do: — run exactly as written.
            > Steps with only DoD reference — discover the right command.
            for STEP in proc:
                if STEP has do: command:
                    do: {STEP.do}
                    check: {STEP.expect}
                    if PASS: emit PASS with evidence
                    if FAIL: => log_failure, continue to next step

                if STEP has only DoD reference (no do: yet):
                    do: attempt to validate the criterion
                    if PASS: emit PASS, => record command as SUCCESS_PATH
                    if FAIL: => log_failure, continue to next step

        6e-2. log_failures
            > Classify every failure from this run. Log before fixing.
            for FAILURE in this run's failures:
                classify: proc_gap | doc_gap | software_bug | infra_issue
                => append to FAILURES_LOG

                if proc_gap or doc_gap:
                    propose fix
                    ask: "Apply fix?"
                    if confirmed: apply, emit FIX

                if software_bug:
                    emit FAIL with evidence
                    ask: "Software bug. Fix application code? (yes/no)"

                if infra_issue:
                    do: docker logs, docker inspect
                    emit FINDING with diagnosis
                    ask: "Infra issue: {diagnosis}. How to proceed?"

        6e-3. update_proc
            > Write back what this run learned.

            > Success path: commands that worked become the proc's steps.
            for STEP where do: was discovered or corrected:
                do: write the exact command that worked as the step's do:
                do: write the condition that proved it as the step's expect:

            > Failure modes: every failure goes into the table.
            for FAILURE in this run's failures:
                do: append to proc's Failure modes table:
                    symptom | cause | fix | learned

            > DoD evidence: write results back to feature README.
            for CRITERION in DODS:
                do: update DoD row:
                    Status: PASS or FAIL (from this run)
                    Evidence: actual output
                    Last checked: now

        6e-4. score
            > CONFIDENCE = sum of weights for passing DoD criteria.
            > Write to state file — the Stop hook blocks exit if below target.
            => CONFIDENCE = sum(DOD.Weight for DOD in DODS if DOD.Status == PASS)
            do: echo '{"mode":"execute","proc":"{CURRENT_PROC}","confidence":{CONFIDENCE},"target":{TARGET}}' > tests2/runs/.proc-state
            emit FINDING "score: {CONFIDENCE}/{TARGET} ({len(passing)}/{len(DODS)} criteria)"

            if CONFIDENCE >= TARGET:
                emit PASS "target reached: {CONFIDENCE}/{TARGET}"
            else:
                emit FINDING "below target — next run will use updated proc"

    if CONFIDENCE < TARGET after max runs:
        emit FAIL "could not reach target: {CONFIDENCE}/{TARGET}"
        ask: "Below target after {max} runs. Continue, switch to author mode, or stop?"

    on_fail: ask

7e. clean_state
    > Leave the system as clean as you found it.
    > Clear the state file so the Stop hook doesn't block future sessions.

    if meeting was opened: call: src/finalize
    if bots were started: do: stop all test bots
    if browser session was created: emit FINDING "browser session left running for reuse"

    do: docker ps --filter "name=vexa-recorder" --format '{{.Names}}'
    expect: no orphan containers

    do: rm -f tests2/runs/.proc-state
    => STATE_CLEAN = true

    call: log.summary(...)
    call: log.close()
    on_fail: ask
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Agent skips steps | No grammar loaded | Step 1 forces read of SPEC.md | Cannot execute what you have not read |
| Agent edits procs while running them | No mode separation | Step 2 sets MODE; author/execute have separate step sequences | Writing the exam and taking the exam are different jobs |
| Agent runs commands while writing procs | Mode drift from author to execute | Author steps never have live system do: commands | If you want to test what you wrote, switch to execute mode explicitly |
| Agent stops after first failure | No convergence loop | Step 6e: repeat until CONFIDENCE >= TARGET | Individual failures don't stop the run — they get logged and scored |
| Agent invents commands for known steps | Didn't follow existing do: | Step 6e-1: if step has do:, run it exactly | Known path is followed, not reinvented |
| Agent doesn't record what worked | No write-back after discovery | Step 6e-3: every validated command becomes the step's do: | Success path grows through execution |
| Failure not logged before fixing | Fix applied without evidence | Step 6e-2: log failure immediately, before proposing fix | The proc's memory comes from logged failures |
| Agent rebuilds everything from scratch | State not probed by tier | Step 5e probes infra→credential→session→ephemeral in order | Probe in dependency order; stop at first failure tier |
| Agent trusts stale state | Validator not run, value assumed valid | Every variable has a concrete validator command in state table | A value exists ≠ a value is live |
| Agent fixes app code without asking | No classification step | Step 6e-2 requires classify before fix | Procs and docs are the agent's domain; app code requires explicit permission |
| Orphan containers after crash | No cleanup step | Step 7e checks and cleans | Always leave state clean, even after failure |
