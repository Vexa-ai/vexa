.PHONY: all lite build up down lite-down docs docs-dev smoke test what-changed full \
       collect score \
       vm-compose vm-lite vm-destroy vm-ssh \
       release-build release-stage release-test release-validate release-ship release-promote \
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
##   0. done                 — dormant; no active release
##   0a. release-worktree    — create ../vexa-<id> git worktree so N
##                             releases run in parallel from one clone
##                             (#229). Run from the main checkout; then
##                             cd into the worktree for every step below.
##   1. release-groom        — scope-design: cluster issues → groom/scope-design
##   2. release-plan         — scope-deliver: scaffold scope artifacts
##   3. release-provision    — *-deliver: provision local/stage infra
##   4. release-deploy       — *-deliver: build + deploy
##   5. release-validate     — *-deliver: run validation matrix
##   6. release-triage       — develop-deliver: classify red feedback
##   7. release-human        — stage-sign: code review + bounded eyeroll
##   8. release-ship         — release-deliver: merge, tag, publish
##   9. release-teardown     — release-sign → done: destroy infra
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

release-worktree:                  ## bootstrap: create ../vexa-<id> worktree + seed done
	@ID=$${ID:?set ID=<YYMMDD-slug>, e.g. ID=260418-webhooks}; \
	bash $(CURDIR)/tests3/lib/worktree.sh create $$ID

release-groom:                     ## scope-design: cluster issues → releases/<id>/groom.md
	@$(_STAGE) assert-is done
	@ID=$${ID:?set ID=<YYMMDD-slug>, e.g. ID=260418-webhooks}; \
	mkdir -p tests3/releases/$$ID; \
	touch tests3/releases/$$ID/groom.md; \
	echo "  created tests3/releases/$$ID/groom.md"; \
	$(_STAGE) enter scope-design --release $$ID --actor make:release-groom; \
	echo "  → next: fill groom.md with issue packs; human approves at least one pack; then \`make release-plan SCOPE=tests3/releases/$$ID/scope.yaml\`"

release-plan:                      ## scope-deliver: scaffold scope.yaml + plan-approval.yaml
	@$(_STAGE) assert-is scope-design
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
	@ID=$${ID:?}; $(_STAGE) enter scope-deliver --release $$ID --actor make:release-plan
	@echo "  → fill scope.yaml + plan-approval.yaml (approved: true on every item) then \`make release-provision SCOPE=tests3/releases/$$ID/scope.yaml\`"

# `develop-design` / `develop-deliver` are entered after scope verification and
# scope sign. Humans and AI then implement within develop-deliver.

release-provision:                 ## deliver: provision VMs + LKE in parallel (LOCAL=1 → docker on this host)
	@python3 -c "import sys; sys.path.insert(0,'tests3/lib'); import stage; s=stage.current(); sys.exit(0 if s.get('stage') in ('develop-deliver','stage-deliver') else (print(f\"stage must be develop-deliver or stage-deliver, got {s.get('stage')}\",file=sys.stderr) or 1))"
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE=tests3/releases/<id>/scope.yaml" && exit 2)
	@MODES="$(_SCOPE_MODES)"; echo "  provisioning modes: $$MODES (LOCAL=$(LOCAL))"; \
	mkdir -p tests3/.state-lite tests3/.state-compose tests3/.state-helm; \
	if [ "$(LOCAL)" = "1" ]; then \
		bash $(CURDIR)/tests3/lib/local-provision.sh "$$MODES"; \
	else \
		for mode in $$MODES; do \
			case $$mode in \
				lite)    $(MAKE) --no-print-directory -C tests3 vm-provision-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
				compose) $(MAKE) --no-print-directory -C tests3 vm-provision-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
				helm)    $(MAKE) --no-print-directory -C tests3 lke-provision lke-setup STATE=$(CURDIR)/tests3/.state-helm & ;; \
			esac; \
		done; wait; \
	fi

release-deploy:                    ## deliver: build + deploy (LOCAL=1 local; non-LOCAL canonical stage)
	@python3 -c "import sys; sys.path.insert(0,'tests3/lib'); import stage; s=stage.current(); ok=('develop-deliver' if '$(LOCAL)' == '1' else 'stage-deliver'); sys.exit(0 if s.get('stage') == ok else (print(f\"stage must be {ok}, got {s.get('stage')}\",file=sys.stderr) or 1))"
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@MODES="$(_SCOPE_MODES)"; echo "  deploying modes: $$MODES (LOCAL=$(LOCAL))"; \
	if [ "$(LOCAL)" = "1" ]; then \
		bash $(CURDIR)/tests3/lib/local-deploy.sh "$$MODES"; \
	else \
		$(MAKE) --no-print-directory release-build; \
		for mode in $$MODES; do \
			case $$mode in \
				lite)    $(MAKE) --no-print-directory -C tests3 vm-redeploy-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
				compose) $(MAKE) --no-print-directory -C tests3 vm-redeploy-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
				helm)    $(MAKE) --no-print-directory -C tests3 lke-upgrade STATE=$(CURDIR)/tests3/.state-helm & ;; \
			esac; \
		done; wait; \
	fi
	@# Deploy is materialization inside develop-deliver or stage-deliver; no
	@# separate deploy state exists in the canonical machine.

release-validate:                  ## deliver: validate matrix inside develop-deliver or stage-deliver
	@python3 -c "import sys; sys.path.insert(0,'tests3/lib'); import stage; s=stage.current(); ok=('develop-deliver' if '$(LOCAL)' == '1' else 'stage-deliver'); sys.exit(0 if s.get('stage') == ok else (print(f\"stage must be {ok}, got {s.get('stage')}\",file=sys.stderr) or 1))"
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@# Validate is hard machine feedback inside the deliver role. Green can
	@# advance to *-verify; red iterates inside *-deliver.
	@if [ "$(LOCAL)" = "1" ]; then \
		$(MAKE) --no-print-directory release-full-local SCOPE=$(SCOPE); \
	else \
		$(MAKE) --no-print-directory release-full SCOPE=$(SCOPE); \
	fi

release-full-local:                ## stage 06 LOCAL variant — on-host matrix (no VM SSH), against localhost
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@MODES="$(_SCOPE_MODES)"; echo "  on-host matrix on modes: $$MODES"; \
	mkdir -p tests3/.state/reports tests3/.state-lite/reports/lite tests3/.state-compose/reports/compose; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 validate-lite STATE=$(CURDIR)/tests3/.state-lite SCOPE=$(CURDIR)/$(SCOPE) & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 validate-compose STATE=$(CURDIR)/tests3/.state-compose SCOPE=$(CURDIR)/$(SCOPE) & ;; \
			helm)    echo "  LOCAL=1 mode 'helm' is not supported on-host; skipping" ;; \
		esac; \
	done; wait
	@MODES="$(_SCOPE_MODES)"; \
	for mode in $$MODES; do \
		case $$mode in \
			lite|compose) bash tests3/tests/walkability-smoke.sh --mode $$mode ;; \
		esac; \
	done
	@$(MAKE) --no-print-directory -C tests3 scope-proof-gate-local SCOPE=$(CURDIR)/$(SCOPE)
	@$(MAKE) --no-print-directory release-report SCOPE=$(SCOPE)

release-triage:                    ## classify failures as regression vs gap inside develop-deliver
	@$(_STAGE) assert-is develop-deliver
	@echo "  invoke the triage skill (or do it by hand): write tests3/releases/<id>/triage-log.md"
	@echo "  once human writes 'fix this first: <DoD-id>', keep iterating in develop-deliver"

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

hot-iterate:                       ## dev loop — rebuild ONE image, recreate on compose only, run scope tests (~5min vs ~30min)
	@test -n "$(SERVICE)" || (echo "  ERROR: set SERVICE=<vexa-bot|dashboard|meeting-api|runtime-api|admin-api|api-gateway|mcp|tts-service|vexa-lite>" && exit 2)
	@bash $(CURDIR)/tests3/lib/hot-iterate.sh "$(SERVICE)" "$(SCOPE)"

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

release-stage:                     ## stage-deliver: canonical provision + :dev deploy + full validate
	@$(_STAGE) assert-is stage-design
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE=tests3/releases/<id>/scope.yaml" && exit 2)
	@if [ "$(LOCAL)" = "1" ]; then echo "  ERROR: release-stage is canonical only; LOCAL=1 belongs to develop-deliver" && exit 2; fi
	@$(_STAGE) enter stage-deliver --actor make:release-stage --reason "stage design complete; begin canonical provision/deploy/validate"
	@MODES="$(_SCOPE_MODES)"; echo "  [stage] provisioning modes: $$MODES"; \
	mkdir -p tests3/.state-lite tests3/.state-compose tests3/.state-helm; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-provision-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-provision-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-provision lke-setup STATE=$(CURDIR)/tests3/.state-helm & ;; \
		esac; \
	done; wait
	@$(MAKE) --no-print-directory release-build
	@MODES="$(_SCOPE_MODES)"; echo "  [stage] deploying :dev to modes: $$MODES"; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-redeploy-lite STATE=$(CURDIR)/tests3/.state-lite & ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-redeploy-compose STATE=$(CURDIR)/tests3/.state-compose & ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-upgrade STATE=$(CURDIR)/tests3/.state-helm & ;; \
		esac; \
	done; wait
	@$(MAKE) --no-print-directory release-full SCOPE=$(SCOPE)
	@echo "  → next: python3 tests3/lib/stage.py enter stage-verify --reason 'canonical validate green'"

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

release-human-sheet:               ## stage-sign sub: generate tests3/releases/<id>/human-checklist.md
	@$(_STAGE) assert-is stage-sign
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@python3 $(CURDIR)/tests3/lib/human-checklist.py generate --scope $(SCOPE)

release-human-gate:                ## stage-sign sub: verify every `- [ ]` is `- [x]`
	@python3 -c "import sys; sys.path.insert(0,'tests3/lib'); import stage; s=stage.current(); sys.exit(0 if s.get('stage') in ('stage-sign','release-deliver') else (print(f\"stage must be stage-sign or release-deliver, got {s.get('stage')}\",file=sys.stderr) or 1))"
	@test -n "$(SCOPE)" || (echo "  ERROR: set SCOPE" && exit 2)
	@python3 $(CURDIR)/tests3/lib/human-checklist.py gate --scope $(SCOPE)

release-human:                     ## stage-sign: generate sheet → human ticks → gate (convenience wrapper)
	@$(MAKE) --no-print-directory release-human-sheet SCOPE=$(SCOPE)
	@echo "  → human: edit tests3/releases/*/human-checklist.md, then re-invoke to gate"
	@$(MAKE) --no-print-directory release-human-gate SCOPE=$(SCOPE)

release-teardown:                  ## done: destroy all provisioned infra after release-sign
	@$(_STAGE) assert-is release-sign
	@MODES="lite compose helm"; \
	if [ -n "$(SCOPE)" ] && [ -f "$(SCOPE)" ]; then MODES="$(_SCOPE_MODES)"; fi; \
	for mode in $$MODES; do \
		case $$mode in \
			lite)    $(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-lite 2>/dev/null || true ;; \
			compose) $(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-compose 2>/dev/null || true ;; \
			helm)    $(MAKE) --no-print-directory -C tests3 lke-destroy STATE=$(CURDIR)/tests3/.state-helm 2>/dev/null || true ;; \
		esac; \
	done
	@$(_STAGE) enter done --actor make:release-teardown --reason "cycle closed"

# ── Compatibility aliases (old names) ──
release-helm-upgrade-safe:         ## v0.10.5.3 Pack H: pre-flight image-exists check + atomic helm upgrade
	@# Captures the outage shape from the v0.10.5.2 ship cycle:
	@# - silent build failures pushed non-existent image tags into helm values
	@# - non-atomic helm upgrade applied them anyway, killed old pods
	@# - replicaCount: 1 + maxUnavailable: 1 left zero pods serving = 502
	@# This target prevents the chain by failing FAST if any image referenced
	@# by the rendered helm values doesn't actually exist on the registry,
	@# then calling: helm upgrade ... --atomic --wait --timeout 5m so a
	@# bad-pod scenario auto-rolls back instead of staying broken.
	@# Required env: RELEASE_NAME, NAMESPACE, KUBECONFIG, KUBE_CONTEXT, CHART_PATH, VALUES_FILES (space-separated)
	@test -n "$(RELEASE_NAME)" || (echo "  ERROR: RELEASE_NAME required" && exit 2)
	@test -n "$(NAMESPACE)" || (echo "  ERROR: NAMESPACE required" && exit 2)
	@test -n "$(CHART_PATH)" || (echo "  ERROR: CHART_PATH required" && exit 2)
	@test -n "$(VALUES_FILES)" || (echo "  ERROR: VALUES_FILES required (space-separated -f files)" && exit 2)
	@VALUES_ARGS=""; for f in $(VALUES_FILES); do VALUES_ARGS="$$VALUES_ARGS -f $$f"; done; \
	echo "  [pre-flight] rendering chart values..."; \
	RENDERED=$$(helm template $(RELEASE_NAME) $(CHART_PATH) $$VALUES_ARGS 2>/dev/null); \
	if [ -z "$$RENDERED" ]; then echo "  ERROR: helm template returned empty" && exit 1; fi; \
	IMAGES=$$(echo "$$RENDERED" | grep -oE 'image:\s+[^\s\"]+' | awk '{print $$2}' | sed 's/^"//;s/"$$//' | sort -u | grep -v '^$$' | grep -v '\$$'); \
	if [ -z "$$IMAGES" ]; then echo "  WARN: no images found in rendered template (check chart)"; fi; \
	echo "  [pre-flight] verifying $$(echo "$$IMAGES" | wc -l) images exist on registry..."; \
	MISSING=""; \
	for img in $$IMAGES; do \
		if docker manifest inspect "$$img" >/dev/null 2>&1; then \
			echo "    OK   $$img"; \
		else \
			echo "    MISS $$img"; \
			MISSING="$$MISSING $$img"; \
		fi; \
	done; \
	if [ -n "$$MISSING" ]; then \
		echo ""; \
		echo "  ABORT: $$(echo $$MISSING | wc -w) image(s) missing on registry — refusing helm upgrade:"; \
		for img in $$MISSING; do echo "    - $$img"; done; \
		exit 1; \
	fi; \
	echo "  [pre-flight] all images present"; \
	echo "  [helm-upgrade] $(RELEASE_NAME) → $(NAMESPACE) (atomic, wait, timeout 5m)..."; \
	helm upgrade $(RELEASE_NAME) $(CHART_PATH) \
		$(if $(KUBECONFIG),--kubeconfig=$(KUBECONFIG),) \
		$(if $(KUBE_CONTEXT),--kube-context=$(KUBE_CONTEXT),) \
		-n $(NAMESPACE) $$VALUES_ARGS \
		--reuse-values=false --atomic --wait --timeout 5m

release-test: release-provision release-deploy release-full  ## alias: full pipeline up through the gate (requires SCOPE)
release-test-no-helm:              ## alias: old 2-VM pipeline (creates a transient scope for compatibility)
	@echo "  release-test-no-helm is deprecated; use release-plan + release-provision + release-full with SCOPE." && exit 2

release-report:                    ## aggregate .state-{lite,compose,helm}/reports/* → tests3/reports/release-<tag>.md
	@mkdir -p tests3/.state/reports
	@# VM modes (lite + compose): reports land at tests3/.state-<mode>/reports/<mode>/ (pulled via vm-run.sh).
	@# helm mode: validate-helm runs locally against STATE=tests3/.state-helm, so reports land at
	@# tests3/.state-helm/reports/helm/ OR tests3/.state/reports/helm/ depending on STATE propagation.
	@# v0.10.6.1 develop-code 2026-05-12: purge stale per-mode reports before
	@# copy. Without this, retired proves' reports (e.g. when a check is
	@# removed mid-cycle) leak into the gate report as ❌ fail despite the
	@# matrix not actually running them. Same pattern as run-matrix.sh's
	@# pre-run purge (Pack U.7 fix), applied at the aggregation step too.
	@for mode in lite compose helm; do \
		mkdir -p tests3/.state/reports/$$mode; \
		find tests3/.state/reports/$$mode -maxdepth 1 -name '*.json' -type f -delete 2>/dev/null || true; \
		for src in tests3/.state-$$mode/reports/$$mode tests3/.state-$$mode/reports/$$mode; do \
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

release-ship:                      ## release-deliver: PR dev→main, promote :dev → :latest
	@$(_STAGE) assert-is release-deliver
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
	@$(_STAGE) enter release-verify --actor make:release-ship

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
