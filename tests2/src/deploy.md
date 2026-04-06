---
needs: []
gives: [DEPLOY_METHODS, ALL_GAPS]
---

use: lib/vm
use: lib/git
use: lib/log
use: env

# Deploy Validation

> **Why:** READMEs rot. A new user following stale docs gets stuck. This catches it.
> **What:** SSH into a blank VM, clone repo, follow the deploy README literally. Every failed instruction is a gap.
> **How:** Provision VM, clone, discover deploy methods from docs, execute each instruction, classify failures (doc gap vs software bug), propose fixes with human approval, push, repeat on fresh VM until a clean run finds zero gaps.

## state

    VM_IP          = ""
    VM_ID          = ""
    REPO_DIR       = ""
    DEPLOY_METHODS = []
    ALL_GAPS       = []
    FIXED_COUNT    = 0

## steps

```
repeat until ALL_GAPS is empty (max 5):

    0. init_log
       call: log.init(COOKBOOK="deploy", RUN_NUMBER={RUN_NUMBER})
       => LOG_FILE, JSONL_FILE

    1. provision
       call: vm.provision(LABEL="vexa-deploy-run{RUN_NUMBER}", REGION={VM_REGION}, TYPE={VM_TYPE})
       => VM_IP, VM_ID
       on_fail: stop

    2. wait
       call: vm.wait_ssh(IP={VM_IP})
       on_fail: stop

    3. clone
       call: vm.ssh(IP={VM_IP}, CMD="git clone {REPO_URL} && cd vexa && git checkout {BRANCH} && pwd")
       expect: exits 0
       => REPO_DIR = "/root/vexa"
       on_fail: stop

    4. orient
       > Read root README. Follow where it points for deployment.
       > Do NOT assume anything. Only follow what docs say.
       call: vm.ssh(IP={VM_IP}, CMD="cat {REPO_DIR}/README.md")
       => ROOT_README

       if ROOT_README mentions deploy:
           follow the path it gives
       else:
           emit FINDING "root README has no deploy path"
           call: vm.ssh(IP={VM_IP}, CMD="find {REPO_DIR} -name 'README.md' -path '*/deploy/*'")

       => DEPLOY_METHODS (list of {name, readme_path})
       on_fail: stop

    for METHOD in DEPLOY_METHODS:

        5. read
           call: vm.ssh(IP={VM_IP}, CMD="cat {METHOD.readme_path}")
           => README_CONTENT
           on_fail: stop

        6. follow
           > Execute every instruction in order. You are a new user
           > on a blank VM. You know nothing except what the README says.

           for INSTRUCTION in README_CONTENT.executable_blocks:

               6a. execute [idempotent]
                   call: vm.ssh(IP={VM_IP}, CMD="{INSTRUCTION.command}")
                   expect: exits 0 or documented result
                   on_fail: fix

                   try:
                       call: vm.ssh(IP={VM_IP}, CMD="{INSTRUCTION.command}")
                   fix:
                       classify: missing_prereq | wrong_command | missing_step | missing_info | software_bug

                       ALL_GAPS.append({run: RUN_NUMBER, method: METHOD.name, type: gap_type, error: error_message, instruction: INSTRUCTION.summary})

                       if gap_type != "software_bug":
                           confirm: "GAP: [{gap_type}] {INSTRUCTION.summary}\n  Error: {error_message}\n  Fix: {proposed_fix}\n  Apply?"
                           if confirmed:
                               apply fix to README on VM
                               FIXED_COUNT += 1
                               emit FIX "{gap_type}: {description}"
                               retry 6a
                           if denied:
                               emit SKIP "human declined"
                       else:
                           emit FAIL "software bug: {error_message}"

        7. alive_check
           > After all instructions: is it actually running?
           call: vm.ssh(IP={VM_IP}, CMD="curl -sf http://localhost:8056/ -o /dev/null && echo UP || echo DOWN")
           emit FINDING "{METHOD.name}: {output}"
           on_fail: continue

    8. commit
       if FIXED_COUNT > 0:
           call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR} && git add -A && git commit -m 'docs: fix deploy gaps from run {RUN_NUMBER}' && git push origin {BRANCH}")
           on_fail: ask

    9. teardown
       call: vm.destroy(VM_ID={VM_ID})
       on_fail: continue

    10. summarize
        call: log.summary(MODULE="deploy", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={len(ALL_GAPS)}, FIXED={FIXED_COUNT}, SKIPPED={skip_count})
        call: log.close()

    11. decide
        if ALL_GAPS is empty:
            call: log.emit(EVENT="PASS", MODULE="deploy", STEP="decide", MSG="clean run — all deploy methods work from fresh VM")

        reset ALL_GAPS for next run
        FIXED_COUNT = 0
```
