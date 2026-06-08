.PHONY: all lite build up down lite-down docs docs-dev smoke test what-changed full \
       collect score bot-debug \
       vm-compose vm-lite vm-destroy vm-ssh \
       deploy validate provision teardown promote publish-packages helm-upgrade-safe \
       help

# ═══ Deploy ═════════════════════════════════════════════════════

all:                               ## full stack via Docker Compose
	@$(MAKE) --no-print-directory -C deploy/compose all

lite:                              ## single-container deploy (Vexa Lite)
	@$(MAKE) --no-print-directory -C deploy/lite all

build:                             ## build + push the :dev artifact (image tag) — the ONLY place bytes are created
	@$(MAKE) --no-print-directory -C deploy/compose build
	@$(MAKE) --no-print-directory -C deploy/compose publish

up:                                ## start compose stack (alias for all)
	@$(MAKE) --no-print-directory -C deploy/compose all

bot-debug:                         ## spawn a bot to MEET_URL in the local stack + tail logs (hot-mounted bot dist)
	@MEET_URL="$(MEET_URL)" bash scripts/bot-debug.sh

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

# ═══ Pipeline verbs ══════════════════════════════════════════════
# The engine's public interface. The stateless `pipeline` skills (infra:*) call
# these via `make -C <repo> <verb> ARG=…`. Verbs are pure functions of their
# args — no stage state, no .current-stage. See tests3/CONTRACT.md and the
# skill-engine-seam doctrine. (The old stage-machine release-* targets were
# removed with the state machine; orchestration now lives in the skills.)

deploy:                            ## deploy(MODE,ENV[,ARTIFACT]): stand the stack up. MODE=lite|compose|helm ENV=local|throwaway|staging|prod
	@test -n "$(MODE)" || { echo "ERROR: set MODE=lite|compose|helm"; exit 2; }
	@test -n "$(ENV)"  || { echo "ERROR: set ENV=local|throwaway|staging|prod"; exit 2; }
	@case "$(ENV)/$(MODE)" in \
	  local/compose)        $(MAKE) --no-print-directory -C deploy/compose all ;; \
	  local/lite)           $(MAKE) --no-print-directory -C deploy/lite all ;; \
	  throwaway/lite)       $(MAKE) --no-print-directory -C tests3 vm-provision-lite vm-redeploy-lite ;; \
	  throwaway/compose)    $(MAKE) --no-print-directory -C tests3 vm-provision-compose vm-redeploy-compose ;; \
	  throwaway/helm)       $(MAKE) --no-print-directory -C tests3 lke-provision lke-setup lke-upgrade ;; \
	  staging/helm|prod/helm) echo "TODO: $(ENV) helm → vexa-platform (cross-repo: see doctrines/helm-validation-env.md). Not in this engine."; exit 2 ;; \
	  *) echo "ERROR: unsupported MODE×ENV: $(MODE)×$(ENV)"; exit 2 ;; \
	esac

validate:                          ## test(MODE,ENV[,SCOPE]): run registry checks against a live stack. MODE=lite|compose|helm
	@test -n "$(MODE)" || { echo "ERROR: set MODE=lite|compose|helm"; exit 2; }
	@case "$(MODE)" in \
	  lite)    $(MAKE) --no-print-directory -C tests3 vm-smoke-lite ;; \
	  compose) $(MAKE) --no-print-directory -C tests3 vm-smoke-compose ;; \
	  helm)    $(MAKE) --no-print-directory -C tests3 lke-smoke ;; \
	  *) echo "ERROR: set MODE=lite|compose|helm"; exit 2 ;; \
	esac

provision:                         ## provision(MODE): stand up fresh ephemeral infra (throwaway VMs / LKE)
	@test -n "$(MODE)" || { echo "ERROR: set MODE=lite|compose|helm"; exit 2; }
	@case "$(MODE)" in \
	  lite)    $(MAKE) --no-print-directory -C tests3 vm-provision-lite ;; \
	  compose) $(MAKE) --no-print-directory -C tests3 vm-provision-compose ;; \
	  helm)    $(MAKE) --no-print-directory -C tests3 lke-provision lke-setup ;; \
	  *) echo "ERROR: set MODE=lite|compose|helm"; exit 2 ;; \
	esac

teardown:                          ## teardown: destroy ephemeral infra (mandatory after a throwaway cycle)
	@$(MAKE) --no-print-directory -C tests3 vm-destroy 2>/dev/null || true
	@$(MAKE) --no-print-directory -C tests3 lke-destroy 2>/dev/null || true

promote:                           ## promote: re-tag the artifact forward (:dev → :latest) — never rebuild
	@$(MAKE) --no-print-directory -C deploy/compose promote-latest

publish-packages:                  ## build + publish every packages/* to npm (idempotent)
	@for dir in packages/*/; do \
		[ -f "$$dir/package.json" ] || continue; \
		NAME=$$(python3 -c "import json; print(json.load(open('$$dir/package.json'))['name'])"); \
		VERSION=$$(python3 -c "import json; print(json.load(open('$$dir/package.json'))['version'])"); \
		LIVE=$$(npm view "$$NAME@$$VERSION" version 2>/dev/null || echo ""); \
		if [ "$$LIVE" = "$$VERSION" ]; then \
			echo "  ✓ $$NAME@$$VERSION already on npm, skipping"; \
		else \
			(cd "$$dir" && npm install --no-audit --no-fund && npm publish) || { echo "  ✗ publish failed for $$NAME@$$VERSION"; exit 1; }; \
			echo "  ✓ $$NAME@$$VERSION published"; \
		fi; \
	done

helm-upgrade-safe:                 ## pre-flight image-exists check + atomic helm upgrade (RELEASE_NAME, NAMESPACE, CHART_PATH, VALUES_FILES)
	@test -n "$(RELEASE_NAME)" || { echo "  ERROR: RELEASE_NAME required"; exit 2; }
	@test -n "$(NAMESPACE)" || { echo "  ERROR: NAMESPACE required"; exit 2; }
	@test -n "$(CHART_PATH)" || { echo "  ERROR: CHART_PATH required"; exit 2; }
	@test -n "$(VALUES_FILES)" || { echo "  ERROR: VALUES_FILES required (space-separated -f files)"; exit 2; }
	@VALUES_ARGS=""; for f in $(VALUES_FILES); do VALUES_ARGS="$$VALUES_ARGS -f $$f"; done; \
	echo "  [pre-flight] rendering chart values..."; \
	RENDERED=$$(helm template $(RELEASE_NAME) $(CHART_PATH) $$VALUES_ARGS 2>/dev/null); \
	if [ -z "$$RENDERED" ]; then echo "  ERROR: helm template returned empty"; exit 1; fi; \
	IMAGES=$$(echo "$$RENDERED" | grep -oE 'image:\s+[^\s\"]+' | awk '{print $$2}' | sed 's/^"//;s/"$$//' | sort -u | grep -v '^$$' | grep -v '\$$'); \
	echo "  [pre-flight] verifying $$(echo "$$IMAGES" | wc -l) images exist on registry..."; \
	MISSING=""; \
	for img in $$IMAGES; do \
		if docker manifest inspect "$$img" >/dev/null 2>&1; then echo "    OK   $$img"; else echo "    MISS $$img"; MISSING="$$MISSING $$img"; fi; \
	done; \
	if [ -n "$$MISSING" ]; then echo "  ABORT: image(s) missing on registry — refusing helm upgrade:"; for img in $$MISSING; do echo "    - $$img"; done; exit 1; fi; \
	echo "  [pre-flight] all images present"; \
	echo "  [helm-upgrade] $(RELEASE_NAME) → $(NAMESPACE) (atomic, wait, timeout 5m)..."; \
	helm upgrade $(RELEASE_NAME) $(CHART_PATH) \
		$(if $(KUBECONFIG),--kubeconfig=$(KUBECONFIG),) \
		$(if $(KUBE_CONTEXT),--kube-context=$(KUBE_CONTEXT),) \
		-n $(NAMESPACE) $$VALUES_ARGS \
		--reuse-values=false --atomic --wait --timeout 5m

# ═══ Util ════════════════════════════════════════════════════════

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F '##' '{printf "  %-20s %s\n", $$1, $$2}'
