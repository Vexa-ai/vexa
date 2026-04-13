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

release-build:                     ## build + publish :dev to DockerHub
	@$(MAKE) --no-print-directory -C deploy/compose build
	@$(MAKE) --no-print-directory -C deploy/compose publish

release-test:                      ## VM test lite + compose in parallel
	@mkdir -p tests3/.state-lite tests3/.state-compose
	@$(MAKE) --no-print-directory -C tests3 vm-lite STATE=$(CURDIR)/tests3/.state-lite & \
	$(MAKE) --no-print-directory -C tests3 vm-compose STATE=$(CURDIR)/tests3/.state-compose & \
	wait && \
	echo "" && \
	echo "  VMs ready for validation:" && \
	echo "  Lite:    http://$$(cat tests3/.state-lite/vm_ip):3000" && \
	echo "  Compose: http://$$(cat tests3/.state-compose/vm_ip):3001" && \
	echo "" && \
	echo "  Run 'make release-validate' after manual validation."

release-validate:                  ## push GitHub status + destroy VMs
	@SHA=$$(git rev-parse HEAD); \
	gh api repos/Vexa-ai/vexa/statuses/$$SHA \
		-f state=success \
		-f context=release/vm-validated \
		-f description="VM tests passed + human validated on $$(date +%Y-%m-%d)" && \
	echo "  ✓ Commit status pushed: release/vm-validated on $$SHA"
	@$(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-lite 2>/dev/null || true
	@$(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-compose 2>/dev/null || true

release-ship:                      ## validate + PR + merge + fix env + promote (after human validation)
	@echo "  ── Step 1: Push validation status + destroy VMs ──"
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
	@TAG=$$(cat deploy/compose/.last-tag); \
	echo "  ══════════════════════════════════════════"; \
	echo "  Release $$TAG shipped."; \
	echo "  :latest = :dev = $$TAG (same SHA)"; \
	echo "  ══════════════════════════════════════════"

release-promote:                   ## promote :dev → :latest on DockerHub (standalone)
	@$(MAKE) --no-print-directory -C deploy/compose promote-latest

# ═══ Util ════════════════════════════════════════════════════════

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F '##' '{printf "  %-20s %s\n", $$1, $$2}'
