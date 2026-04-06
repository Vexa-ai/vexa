---
needs: []
gives: [DEPLOY_METHODS, ALL_GAPS]
---

use: env
use: lib/log

# Deploy Validation

> **Why:** The deploy README is the first thing a new user reads. If it's wrong, they can't get started.
> **What:** Provision a fresh VM, clone repo, follow deploy README literally, find every gap, fix with human approval, push, repeat until clean.
> **How:** Calls src/deploy which loops: provision -> clone -> follow instructions -> classify failures -> fix docs -> push -> destroy VM -> repeat.

## steps

```
1. init_log
   call: log.init(COOKBOOK="deploy")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="deploy", STEP="init", MSG="starting deploy validation")

2. validate
   call: src/deploy
   => DEPLOY_METHODS, ALL_GAPS
   call: log.emit(EVENT="FINDING", MODULE="deploy", STEP="validate", MSG="methods={len(DEPLOY_METHODS)} gaps={len(ALL_GAPS)}")

3. finish
   call: log.summary(MODULE="deploy", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={len(ALL_GAPS)}, FIXED={fixed_count}, SKIPPED={skip_count})
   call: log.close()
   call: log.emit(EVENT="FINDING", MODULE="deploy", STEP="finish", MSG="logs at {LOG_FILE}")
```
