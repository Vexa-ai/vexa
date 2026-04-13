.PHONY: all lite build up down lite-down docs smoke test what-changed full \
       collect score \
       vm-compose vm-lite vm-destroy vm-ssh \
       release-build release-test release-validate release-promote \
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
	@$(MAKE) --no-print-directory -C tests3 vm-lite STATE=$(CURDIR)/tests3/.state-lite &
	@$(MAKE) --no-print-directory -C tests3 vm-compose STATE=$(CURDIR)/tests3/.state-compose &
	@wait
	@echo ""
	@echo "  VMs ready for validation:"
	@echo "  Lite:    http://$$(cat tests3/.state-lite/vm_ip):3000"
	@echo "  Compose: http://$$(cat tests3/.state-compose/vm_ip):3001"
	@echo ""
	@echo "  Run 'make release-validate' after manual validation."

release-validate:                  ## push GitHub status + destroy VMs
	@SHA=$$(git rev-parse HEAD); \
	gh api repos/Vexa-ai/vexa/statuses/$$SHA \
		-f state=success \
		-f context=release/vm-validated \
		-f description="VM tests passed + human validated on $$(date +%Y-%m-%d)" && \
	echo "  ✓ Commit status pushed: release/vm-validated on $$SHA"
	@$(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-lite 2>/dev/null || true
	@$(MAKE) --no-print-directory -C tests3 vm-destroy STATE=$(CURDIR)/tests3/.state-compose 2>/dev/null || true

release-promote:                   ## promote :dev → :latest on DockerHub
	@$(MAKE) --no-print-directory -C deploy/compose promote-latest

# ═══ Util ════════════════════════════════════════════════════════

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F '##' '{printf "  %-20s %s\n", $$1, $$2}'
