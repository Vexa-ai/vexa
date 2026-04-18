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
## Release cycle — scope-driven 7-stage process (see tests3/README.md)
##
##   1. release-plan        — scaffold scope.yaml (one-off per release)
##   2. release-provision   — parallel: provision VMs + LKE per scope.deployments.modes
##   3. (develop code + tests locally, in parallel with #2)
##   4. release-deploy      — build :dev + push + redeploy to each provisioned mode
##   5. release-iterate     — targeted tests (scope-filtered); loop until green
##   6. release-full        — fresh-reset all modes + run full matrix + gate
##   7. release-ship        — push GitHub status + PR dev→main + promote :latest
##   8. release-teardown    — destroy all provisioned infra
##
## Each stage is ONE command. Scope drives: SCOPE=tests3/releases/<id>/scope.yaml
## ─────────────────────────────────────────────────────────────────────

# Resolve which modes this scope touches (used by every stage below).
define _SCOPE_MODES
$$(python3 -c "import yaml,sys; s=yaml.safe_load(open('$(SCOPE)')); print(' '.join(s['deployments']['modes']))")
endef

release-plan:                      ## stage 1: scaffold tests3/releases/<ID>/scope.yaml
	@ID=$${ID:?set ID=<slug>, e.g. ID=260417-webhooks-dbpool}; \
	mkdir -p tests3/releases/$$ID; \
	if [ -f tests3/releases/$$ID/scope.yaml ]; then \
		echo "  scope already exists: tests3/releases/$$ID/scope.yaml"; \
	else \
		cp tests3/releases/_template/scope.yaml tests3/releases/$$ID/scope.yaml; \
		sed -i "s/REPLACE-WITH-YYMMDD-SLUG/$$ID/" tests3/releases/$$ID/scope.yaml; \
		echo "  created tests3/releases/$$ID/scope.yaml — fill in issues + fix_commits"; \
	fi
	@echo "  → next: export SCOPE=tests3/releases/$$ID/scope.yaml"

release-provision:                 ## stage 2: provision deployments in parallel per scope
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

release-deploy:                    ## stage 4: build + push :dev + redeploy to all provisioned modes
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

release-iterate:                   ## stage 5: run scope-filtered targeted tests on all modes + aggregate
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

release-full:                      ## stage 6: fresh-reset + full cheap-tier matrix on all modes + aggregate + gate
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

release-human-sheet:               ## stage 6b: generate tests3/releases/<id>/human-checklist.md (always + scope-specific)
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@python3 $(CURDIR)/tests3/lib/human-checklist.py generate --scope $(SCOPE)

release-human-gate:                ## verify the human checklist — every `- [ ]` must be `- [x]`
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@python3 $(CURDIR)/tests3/lib/human-checklist.py gate --scope $(SCOPE)

release-teardown:                  ## stage 8: destroy all provisioned infra (call AFTER release-ship)
	@MODES="lite compose helm"; \
	if [ -n "$(SCOPE)" ] && [ -f "$(SCOPE)" ]; then MODES="$(_SCOPE_MODES)"; fi; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-lite 2>/dev/null || true ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-compose 2>/dev/null || true ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-destroy STATE=$(CURDIR)/tests3/.state-helm 2>/dev/null || true ;; \
		esac; \
	done

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

release-validate:                  ## push GitHub status + destroy VMs + destroy LKE cluster
	@SHA=$$(git rev-parse HEAD); \
	gh api repos/Vexa-ai/vexa/statuses/$$SHA \
		-f state=success \
		-f context=release/vm-validated \
		-f description="VM+helm tests passed + report gate on $$(date +%Y-%m-%d)" && \
	echo "  ✓ Commit status pushed: release/vm-validated on $$SHA"
	@$(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-lite 2>/dev/null || true
	@$(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-compose 2>/dev/null || true
	@$(MAKE) --no-print-directory -C tests3 lke-destroy STATE=$(CURDIR)/tests3/.state-helm 2>/dev/null || true

release-ship:                      ## stage 7: verify both gates + PR dev→main + promote :latest
	@echo "  ── Stage 7.1: Human checklist gate ──"
	@if [ -n "$(SCOPE)" ]; then \
		$(MAKE) --no-print-directory release-human-gate SCOPE=$(SCOPE); \
	else \
		echo "  SKIP: no SCOPE given (legacy flow — human gate not enforced)"; \
	fi
	@echo "  ── Stage 7.2: Push GitHub validation status ──"
	@$(MAKE) --no-print-directory release-validate
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
	@echo ""
	@echo "  ── Step 5: Switch back to dev ──"
	@git checkout dev && git merge main --no-edit
	@TAG=$$(cat deploy/compose/.last-tag); \
	echo ""; \
	echo "  ══════════════════════════════════════════"; \
	echo "  Release $$TAG shipped."; \
	echo "  :latest = :dev = $$TAG (same SHA)"; \
	echo "  Now on dev branch. Ready for next cycle."; \
	echo "  ══════════════════════════════════════════"

release-promote:                   ## promote :dev → :latest on DockerHub (standalone)
	@$(MAKE) --no-print-directory -C deploy/compose promote-latest

# ═══ Util ════════════════════════════════════════════════════════

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F '##' '{printf "  %-20s %s\n", $$1, $$2}'
