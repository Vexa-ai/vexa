.PHONY: all lite build up down lite-down docs docs-dev smoke test what-changed full \
       collect score \
       vm-compose vm-lite vm-destroy vm-ssh \
       release-build release-test release-validate release-ship release-promote \
       help

# ═══ Deploy ═════════════════════════════════════════════════════

all:                               ## full stack via Docker Compose
	@$(MAKE) --no-print-directory -C deploy/compose all

lite:                              ## single-container deploy (Vexa Lite)
	@$(MAKE) --no-print-directory -C deploy/lite all

build:                             ## build all images from source
	@$(MAKE) --no-print-directory -C deploy/compose build

up:                                ## start compose stack (alias for all)
	@$(MAKE) --no-print-directory -C deploy/compose all

down:                              ## stop compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down

lite-down:                         ## stop lite containers
	@$(MAKE) --no-print-directory -C deploy/lite down

# ═══ Test ════════════════════════════════════════════════════════

docs:                              ## check docs for drift (static, 0s)
	@$(MAKE) --no-print-directory -C tests3 docs

docs-dev:                          ## start mintlify dev server on localhost:3000
	@$(MAKE) --no-print-directory -C docs dev

smoke:                             ## run all checks (~30s)
	@$(MAKE) --no-print-directory -C tests3 smoke

test:                              ## resolve changed files → run affected tests
	@$(MAKE) --no-print-directory -C tests3 what-changed
	@TARGETS=$$(git diff --name-only $${BASE:-main} | python3 tests3/resolve.py 2>/dev/null); \
	if [ -n "$$TARGETS" ]; then \
		$(MAKE) --no-print-directory -C tests3 $$TARGETS; \
	else \
		echo "No test targets affected. Running smoke."; \
		$(MAKE) --no-print-directory -C tests3 smoke; \
	fi

what-changed:                      ## show which tests would run (dry-run)
	@$(MAKE) --no-print-directory -C tests3 what-changed

full:                              ## run everything
	@$(MAKE) --no-print-directory -C tests3 full

# ═══ Data collection ════════════════════════════════════════════

collect:                           ## collect dataset from live meeting (CONVERSATION=3speakers)
	@$(MAKE) --no-print-directory -C tests3 collect CONVERSATION=$${CONVERSATION:-3speakers}

score:                             ## re-score existing dataset offline (DATASET=gmeet-compose-260405)
	@$(MAKE) --no-print-directory -C tests3 score DATASET=$${DATASET}

# ═══ VM ══════════════════════════════════════════════════════════

vm-compose:                        ## fresh VM + compose + smoke
	@$(MAKE) --no-print-directory -C tests3 vm-compose

vm-lite:                           ## fresh VM + lite + smoke
	@$(MAKE) --no-print-directory -C tests3 vm-lite

vm-destroy:                        ## tear down VM
	@$(MAKE) --no-print-directory -C tests3 vm-destroy

vm-ssh:                            ## SSH into VM
	@$(MAKE) --no-print-directory -C tests3 vm-ssh

# ═══ Release ═════════════════════════════════════════════════════

release-build:                     ## build + publish :dev to DockerHub + record tag
	@$(MAKE) --no-print-directory -C deploy/compose build
	@$(MAKE) --no-print-directory -C deploy/compose publish
	@# Record the freshly-built tag so release-test can propagate it into per-mode state
	@# (deploy/compose/.last-tag is written by the publish step)
	@mkdir -p tests3/.state tests3/.state-lite tests3/.state-compose tests3/.state-helm
	@if [ -f deploy/compose/.last-tag ]; then \
		TAG=$$(cat deploy/compose/.last-tag); \
		echo "$$TAG" > tests3/.state/image_tag; \
		echo "$$TAG" > tests3/.state-lite/image_tag; \
		echo "$$TAG" > tests3/.state-compose/image_tag; \
		echo "$$TAG" > tests3/.state-helm/image_tag; \
	fi

## ─────────────────────────────────────────────────────────────────────
## Release cycle — stage state machine (see tests3/README.md §5.5)
##
##   0. idle                 — dormant; no active release
##   0a. release-worktree    — create ../vexa-<id> git worktree so N
##                             releases run in parallel from one clone
##                             (#229). Run from the main checkout; then
##                             cd into the worktree for every step below.
##   1. release-groom        — cluster issues → groom.md (AI, stage 01)
##   2. release-plan         — scaffold scope.yaml + plan-approval.yaml (stage 02)
##   3. release-provision    — VMs + LKE (stage 04)
##   4. release-deploy       — build :dev + push + redeploy (stage 05)
##   5. release-validate     — three-phase validate → Gate verdict (stage 06)
##        on red  → release-triage
##        on green → release-human
##   6. release-triage       — classify regression vs gap (stage 07)
##   7. release-human        — code review + bounded eyeroll (stage 08)
##   8. release-ship         — merge dev→main; promote :latest (stage 09)
##   9. release-teardown     — destroy infra (stage 10)
##
## Every target asserts stage before acting, transitions stage on success.
## Scope drives: SCOPE=tests3/releases/<id>/scope.yaml
## ─────────────────────────────────────────────────────────────────────

# Resolve which modes this scope touches (used by every stage below).
define _SCOPE_MODES
$$(python3 -c "import yaml,sys; s=yaml.safe_load(open('$(SCOPE)')); print(' '.join(s['deployments']['modes']))")
endef

# Stage helper — every release-* target calls this before + after work.
_STAGE = python3 $(CURDIR)/tests3/lib/stage.py

stage:                             ## print current stage + next
	@$(_STAGE) probe

release-worktree:                  ## stage 00→ bootstrap: create ../vexa-<id> worktree + seed idle
	@ID=$${ID:?set ID=<YYMMDD-slug>, e.g. ID=260418-webhooks}; \
	bash $(CURDIR)/tests3/lib/worktree.sh create $$ID

release-groom:                     ## stage 01: cluster issues → releases/<id>/groom.md
	@$(_STAGE) assert-is idle
	@ID=$${ID:?set ID=<YYMMDD-slug>, e.g. ID=260418-webhooks}; \
	mkdir -p tests3/releases/$$ID; \
	touch tests3/releases/$$ID/groom.md; \
	echo "  created tests3/releases/$$ID/groom.md"; \
	$(_STAGE) enter groom --release $$ID --actor make:release-groom; \
	echo "  → next: fill groom.md with issue packs; human approves at least one pack; then \`make release-plan SCOPE=tests3/releases/$$ID/scope.yaml\`"

release-plan:                      ## stage 02: scaffold scope.yaml + plan-approval.yaml
	@$(_STAGE) assert-is groom
	@ID=$${ID:?set ID=<YYMMDD-slug>}; \
	mkdir -p tests3/releases/$$ID; \
	if [ -f tests3/releases/$$ID/scope.yaml ]; then \
		echo "  scope already exists: tests3/releases/$$ID/scope.yaml"; \
	else \
		cp tests3/releases/_template/scope.yaml tests3/releases/$$ID/scope.yaml; \
		sed -i "s/REPLACE-WITH-YYMMDD-SLUG/$$ID/" tests3/releases/$$ID/scope.yaml; \
		echo "  created tests3/releases/$$ID/scope.yaml"; \
	fi
	@ID=$${ID:?}; touch tests3/releases/$$ID/plan-approval.yaml
	@ID=$${ID:?}; $(_STAGE) enter plan --release $$ID --actor make:release-plan
	@echo "  → fill scope.yaml + plan-approval.yaml (approved: true on every item) then \`make release-provision SCOPE=tests3/releases/$$ID/scope.yaml\`"

# Stage 03 `develop` has no Makefile target — it's "humans write code" with
# AI assist. The stage is entered by release-plan (once plan-approval is
# signed) via a helper; for now it's entered manually via stage.py enter develop.

release-provision:                 ## stage 04: provision VMs + LKE in parallel
	@$(_STAGE) assert-is develop
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE=tests3/releases/<id>/scope.yaml" && exit 2)
	@MODES="$(_SCOPE_MODES)"; echo "  provisioning modes: $$MODES"; \
	mkdir -p tests3/.state-lite tests3/.state-compose tests3/.state-helm; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-provision-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-provision-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-provision lke-setup STATE=$(CURDIR)/tests3/.state-helm & ;; \
		esac; \
	done; wait
	@$(_STAGE) enter provision --actor make:release-provision

release-deploy:                    ## stage 05: build + push :dev + redeploy to all provisioned modes
	@python3 -c "import sys; from pathlib import Path; sys.path.insert(0,'tests3/lib'); import stage; s=stage.current(); sys.exit(0 if s.get('stage') in ('provision','develop') else (print(f\"stage must be provision or develop, got {s.get('stage')}\",file=sys.stderr) or 1))"
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@$(MAKE) --no-print-directory release-build
	@MODES="$(_SCOPE_MODES)"; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-redeploy-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-redeploy-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-upgrade STATE=$(CURDIR)/tests3/.state-helm & ;; \
		esac; \
	done; wait
	@$(_STAGE) enter deploy --actor make:release-deploy

release-validate:                  ## stage 06: three-phase validate → Gate verdict (green→human / red→triage)
	@$(_STAGE) assert-is deploy
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@$(MAKE) --no-print-directory release-full SCOPE=$(SCOPE) && \
		($(_STAGE) enter human --actor make:release-validate --reason "gate green" && echo "  → stage: human") || \
		($(_STAGE) enter triage --actor make:release-validate --reason "gate red" && echo "  → stage: triage" && exit 1)

release-triage:                    ## stage 07: classify failures as regression vs gap
	@$(_STAGE) assert-is triage
	@echo "  invoke the triage skill (or do it by hand): write tests3/releases/<id>/triage-log.md"
	@echo "  once human writes 'fix this first: <DoD-id>' run: python3 tests3/lib/stage.py enter develop --reason 'triage picked next fix'"

release-iterate:                   ## stage 06 fast variant — scope-filtered tests (dev loop, not authoritative)
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@MODES="$(_SCOPE_MODES)"; \
	mkdir -p tests3/.state; cp -f $(SCOPE) tests3/.state/scope.yaml; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-validate-scope-lite STATE=$(CURDIR)/tests3/.state-lite SCOPE=$(CURDIR)/$(SCOPE) & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-validate-scope-compose STATE=$(CURDIR)/tests3/.state-compose SCOPE=$(CURDIR)/$(SCOPE) & ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 validate-helm STATE=$(CURDIR)/tests3/.state-helm SCOPE=$(CURDIR)/$(SCOPE) & ;; \
		esac; \
	done; wait
	@$(MAKE) --no-print-directory release-report

release-reset:                     ## stage 6a: wipe stack+volumes on all provisioned modes (keeps VMs/cluster)
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@MODES="$(_SCOPE_MODES)"; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-reset-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-reset-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
			helm)    bash $(CURDIR)/tests3/lib/reset/reset-helm.sh STATE=$(CURDIR)/tests3/.state-helm & ;; \
		esac; \
	done; wait

release-full:                      ## stage 06 authoritative variant — fresh-reset + full matrix + gate
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@$(MAKE) --no-print-directory release-reset SCOPE=$(SCOPE)
	@MODES="$(_SCOPE_MODES)"; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-smoke-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-smoke-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-smoke STATE=$(CURDIR)/tests3/.state-helm SCOPE= & ;; \
		esac; \
	done; wait
	@$(MAKE) --no-print-directory release-report

release-issue-add:                 ## add an issue to scope.yaml (enforces gap_analysis + new_checks when SOURCE=human)
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE=tests3/releases/<id>/scope.yaml" && exit 2)
	@test -n "$(ID)" || (echo "  ERROR: set ID=<bug-slug>" && exit 2)
	@test -n "$(SOURCE)" || (echo "  ERROR: set SOURCE=human|gh-issue|internal|regression" && exit 2)
	@test -n "$(PROBLEM)" || (echo "  ERROR: set PROBLEM='...'" && exit 2)
	@python3 $(CURDIR)/tests3/lib/release-issue-add.py \
	  --scope $(SCOPE) --id "$(ID)" --source "$(SOURCE)" --problem "$(PROBLEM)" \
	  $(if $(REF),--ref "$(REF)") \
	  $(if $(HYPOTHESIS),--hypothesis "$(HYPOTHESIS)") \
	  $(if $(GAP),--gap "$(GAP)") \
	  $(if $(NEW_CHECKS),--new-checks "$(NEW_CHECKS)") \
	  $(if $(MODES),--modes "$(MODES)") \
	  $(if $(HV_MODE),--human-verify-mode "$(HV_MODE)") \
	  $(if $(HV_DO),--human-verify-do "$(HV_DO)") \
	  $(if $(HV_EXPECT),--human-verify-expect "$(HV_EXPECT)")

release-human-sheet:               ## stage 08 sub: generate tests3/releases/<id>/human-checklist.md
	@$(_STAGE) assert-is human
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@python3 $(CURDIR)/tests3/lib/human-checklist.py generate --scope $(SCOPE)

release-human-gate:                ## stage 08 sub: verify every `- [ ]` is `- [x]`
	@$(_STAGE) assert-is human
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@python3 $(CURDIR)/tests3/lib/human-checklist.py gate --scope $(SCOPE)

release-human:                     ## stage 08: generate sheet → human ticks → gate (convenience wrapper)
	@$(MAKE) --no-print-directory release-human-sheet SCOPE=$(SCOPE)
	@echo "  → human: edit tests3/releases/*/human-checklist.md, then re-invoke to gate"
	@$(MAKE) --no-print-directory release-human-gate SCOPE=$(SCOPE)

release-teardown:                  ## stage 10: destroy all provisioned infra (after release-ship)
	@$(_STAGE) assert-is ship
	@MODES="lite compose helm"; \
	if [ -n "$(SCOPE)" ] && [ -f "$(SCOPE)" ]; then MODES="$(_SCOPE_MODES)"; fi; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-lite 2>/dev/null || true ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-compose 2>/dev/null || true ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-destroy STATE=$(CURDIR)/tests3/.state-helm 2>/dev/null || true ;; \
		esac; \
	done
	@$(_STAGE) enter teardown --actor make:release-teardown
	@$(_STAGE) enter idle --actor make:release-teardown --reason "cycle closed"

# ── Compatibility aliases (old names) ──
release-test: release-provision release-deploy release-full  ## alias: full pipeline up through the gate (requires SCOPE)
release-test-no-helm:              ## alias: old 2-VM pipeline (creates a transient scope for compatibility)
	@echo "  release-test-no-helm is deprecated; use release-plan + release-provision + release-full with SCOPE." && exit 2

release-report:                    ## aggregate .state-{lite,compose,helm}/reports/* → tests3/reports/release-<tag>.md
	@mkdir -p tests3/.state/reports
	@# VM modes (lite + compose): reports land at tests3/.state-<mode>/reports/<mode>/ (pulled via vm-run.sh).
	@# helm mode: validate-helm runs locally against STATE=tests3/.state-helm, so reports land at
	@# tests3/.state-helm/reports/helm/ OR tests3/.state/reports/helm/ depending on STATE propagation.
	@for mode in lite compose helm; do \
		mkdir -p tests3/.state/reports/$$mode; \
		for src in tests3/.state-$$mode/reports/$$mode tests3/.state/reports/$$mode; do \
			[ -d "$$src" ] && find "$$src" -maxdepth 1 -name "*.json" -exec cp {} tests3/.state/reports/$$mode/ \; 2>/dev/null || true; \
		done; \
	done
	@for mode in lite compose helm; do \
		if [ -f "tests3/.state-$$mode/image_tag" ]; then \
			cp tests3/.state-$$mode/image_tag tests3/.state/image_tag; \
			break; \
		fi; \
	done
	@TAG=$$(cat tests3/.state/image_tag 2>/dev/null || echo "unknown"); \
	SCOPE_ARG=""; \
	if [ -n "$(SCOPE)" ] && [ -f "$(SCOPE)" ]; then SCOPE_ARG="--scope $(SCOPE)"; fi; \
	python3 tests3/lib/aggregate.py --write-features \
		--out tests3/reports/release-$$TAG.md \
		$$SCOPE_ARG --gate-check && \
		echo "" && echo "  Release gate PASSED. Report → tests3/reports/release-$$TAG.md" || \
		(echo "" && echo "  Release gate FAILED — see tests3/reports/release-$$TAG.md" && exit 1)

release-gh-status:                 ## internal: push `release/vm-validated` GitHub commit status
	@SHA=$$(git rev-parse HEAD); \
	gh api repos/Vexa-ai/vexa/statuses/$$SHA \
		-f state=success \
		-f context=release/vm-validated \
		-f description="VM+helm tests passed + report gate on $$(date +%Y-%m-%d)" && \
	echo "  ✓ Commit status pushed: release/vm-validated on $$SHA"

release-ship:                      ## stage 09: PR dev→main, promote :dev → :latest
	@$(_STAGE) assert-is human
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@echo "  ── 1. human gate (re-verify) ──"
	@$(MAKE) --no-print-directory release-human-gate SCOPE=$(SCOPE)
	@echo "  ── 2. push GitHub validation status ──"
	@$(MAKE) --no-print-directory release-gh-status
	@echo ""
	@echo "  ── Step 2: Create + merge PR ──"
	@TAG=$$(cat deploy/compose/.last-tag); \
	EXISTING=$$(gh pr list --head dev --base main --json number --jq '.[0].number' 2>/dev/null); \
	if [ -n "$$EXISTING" ]; then \
		echo "  PR #$$EXISTING already exists, merging..."; \
		gh pr merge $$EXISTING --merge; \
	else \
		gh pr create --base main --head dev \
			--title "Release $$TAG" \
			--body "Validated release $$TAG" && \
		EXISTING=$$(gh pr list --head dev --base main --json number --jq '.[0].number'); \
		gh pr merge $$EXISTING --merge; \
	fi
	@echo ""
	@echo "  ── Step 3: Fix env-example on main ──"
	@git checkout main && git pull && \
	sed -i 's|^IMAGE_TAG=dev|IMAGE_TAG=latest|' deploy/env-example && \
	sed -i 's|^BROWSER_IMAGE=vexaai/vexa-bot:dev|BROWSER_IMAGE=vexaai/vexa-bot:latest|' deploy/env-example && \
	git add deploy/env-example && \
	git commit -m "fix: restore IMAGE_TAG=latest on main after dev merge" && \
	git push origin main
	@echo ""
	@echo "  ── Step 4: Promote :latest ──"
	@$(MAKE) --no-print-directory -C deploy/compose promote-latest
	@echo ""
	@echo "  ── Step 5: Publish packages to npm ──"
	@$(MAKE) --no-print-directory release-publish-packages
	@echo ""
	@echo "  ── Step 6: Switch back to dev ──"
	@git checkout dev && git merge main --no-edit
	@TAG=$$(cat deploy/compose/.last-tag); \
	echo ""; \
	echo "  ══════════════════════════════════════════"; \
	echo "  Release $$TAG shipped."; \
	echo "  :latest = :dev = $$TAG (same SHA)"; \
	echo "  Now on dev branch. Ready for next cycle."; \
	echo "  ══════════════════════════════════════════"
	@$(_STAGE) enter ship --actor make:release-ship

release-promote:                   ## promote :dev → :latest on DockerHub (standalone)
	@$(MAKE) --no-print-directory -C deploy/compose promote-latest

release-publish-packages:          ## build + publish every packages/* to npm (idempotent)
	@for dir in packages/*/; do \
		[ -f "$$dir/package.json" ] || continue; \
		NAME=$$(python3 -c "import json; print(json.load(open('$$dir/package.json'))['name'])"); \
		VERSION=$$(python3 -c "import json; print(json.load(open('$$dir/package.json'))['version'])"); \
		echo ""; \
		echo "  ── publishing $$NAME@$$VERSION ──"; \
		LIVE=$$(npm view "$$NAME@$$VERSION" version 2>/dev/null || echo ""); \
		if [ "$$LIVE" = "$$VERSION" ]; then \
			echo "  ✓ $$NAME@$$VERSION already on npm, skipping"; \
		else \
			(cd "$$dir" && npm install --no-audit --no-fund && npm publish) || \
				{ echo "  ✗ publish failed for $$NAME@$$VERSION"; exit 1; }; \
			echo "  ✓ $$NAME@$$VERSION published"; \
		fi; \
	done

# ═══ Util ════════════════════════════════════════════════════════

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F '##' '{printf "  %-20s %s\n", $$1, $$2}'
