.PHONY: detect docs locks env health contracts smoke \
       dashboard-auth dashboard-proxy dashboard \
       containers browser-session browser-login browser \
       meeting-tts meeting bot admit transcribe finalize post-meeting meeting-chain \
       webhooks auth-meeting bot-stop-timing transcription-replay tts-reliability \
       collect score \
       validate-lite validate-compose validate-helm validate-all \
       report report-gate \
       e2e full \
       vm-compose vm-lite \
       vm-provision-compose vm-provision-lite vm-smoke-compose vm-smoke-lite \
       vm-destroy vm-ssh vm-status \
       lke-helm lke-provision lke-setup lke-smoke lke-destroy lke-status \
       helm-check helm-smoke helm-dashboard helm-containers helm-full \
       what-changed \
       clean help

SHELL := /bin/bash
ROOT  := $(shell git rev-parse --show-toplevel)
T3    := $(ROOT)/tests3
STATE := $(T3)/.state

# Override for non-local deployments (e.g. DEPLOY_MODE=helm GATEWAY_URL=https://...)
DEPLOY_MODE   ?=
GATEWAY_URL   ?=
ADMIN_URL     ?=
DASHBOARD_URL ?=

# Auto-detect LKE kubeconfig if provisioned
LKE_KUBECONFIG := $(shell cat $(STATE)/lke_kubeconfig_path 2>/dev/null)
ifneq ($(LKE_KUBECONFIG),)
  export KUBECONFIG := $(LKE_KUBECONFIG)
endif

export ROOT DEPLOY_MODE GATEWAY_URL ADMIN_URL DASHBOARD_URL STATE MEETING_URL

# Smoke runs once per session via a stamp file.
# `make clean` resets it. Individual tier targets always run.
SMOKE_STAMP := $(STATE)/.smoke-passed

# ═══ Checks (registry-driven) ════════════════════════════════════

detect:                           ## auto-detect deploy mode and URLs
	@$(T3)/lib/detect.sh

docs:                             ## doc drift checks (static, 0s)
	@python3 $(T3)/docs/check.py

locks:                            ## static regression locks (0s)
	@$(T3)/checks/run --tier static

env: detect                       ## env var consistency (2s)
	@$(T3)/checks/run --tier env

health: detect                    ## service endpoints respond (5s)
	@$(T3)/checks/run --tier health

contracts: detect                 ## API behavior (15s)
	@$(T3)/checks/run --tier contract

smoke: docs locks env health contracts ## all checks, no meetings (30s)
	@touch $(SMOKE_STAMP)
	@echo ""; echo "  Smoke passed."

# Gate: run smoke once, skip if already passed this session
$(SMOKE_STAMP):
	@$(MAKE) --no-print-directory smoke

# ═══ Dashboard ════════════════════════════════════════════════════

dashboard-auth: $(SMOKE_STAMP)    ## login + identity + cookie (5s)
	@$(T3)/tests/dashboard-auth.sh

dashboard-proxy: dashboard-auth   ## proxy: meetings, pagination, transcript, bot create (15s)
	@$(T3)/tests/dashboard-proxy.sh

dashboard: dashboard-proxy        ## all dashboard tests

# ═══ Container lifecycle ══════════════════════════════════════════

containers: $(SMOKE_STAMP)        ## create/stop/remove, timeout, orphans (60s)
	@$(T3)/tests/containers.sh

# ═══ Browser session ══════════════════════════════════════════════

browser-session: $(SMOKE_STAMP)   ## CDP, S3 roundtrip, auto-save (120s)
	@$(T3)/tests/browser-session.sh

browser-login: browser-session    ## [human] Google login persistence
	@$(T3)/tests/browser-login.sh

browser: browser-session          ## browser without human gate

# ═══ Meeting chain ════════════════════════════════════════════════

meeting-tts: $(SMOKE_STAMP)       ## full meeting: create → bots → admit → TTS → transcript (10min)
	@$(T3)/tests/meeting-tts.sh

meeting-tts-teams: $(SMOKE_STAMP) ## Teams meeting: bots → admit → TTS → transcript (TEAMS_MEETING_URL required)
	@$(T3)/tests/meeting-tts-teams.sh

transcription-replay: $(SMOKE_STAMP)  ## replay GT transcript as TTS, score accuracy (MEETING_URL required, ~7min)
	@$(T3)/tests/transcription-replay.sh

bot-stop-timing: $(SMOKE_STAMP)   ## measure bot stop lifecycle timing (MEETING_URL required)
	@$(T3)/tests/bot-stop-timing.sh

tts-reliability: $(SMOKE_STAMP)   ## test TTS reliability: 10 speak commands, check transcript (MEETING_URL required)
	@$(T3)/tests/tts-reliability.sh

meeting: $(SMOKE_STAMP)           ## create meeting via CDP (60s)
	@$(T3)/tests/meeting.sh

bot: meeting                      ## launch recorder, poll until active (5min)
	@$(T3)/tests/bot.sh

admit: bot                        ## auto-admit bot via CDP (30s)
	@$(T3)/tests/admit.sh

transcribe: admit                 ## send TTS, fetch transcript, score (2min)
	@$(T3)/tests/transcribe.sh

finalize: transcribe              ## stop bots, verify cleanup (30s)
	@$(T3)/tests/finalize.sh

post-meeting: finalize            ## recordings, deferred transcription (60s)
	@$(T3)/tests/post-meeting.sh

meeting-chain: post-meeting       ## full meeting lifecycle (step-by-step)

# ═══ Webhooks ═════════════════════════════════════════════════════

webhooks: $(SMOKE_STAMP)          ## envelope, HMAC, no secret leak (10s)
	@$(T3)/tests/webhooks.sh

# ═══ Authenticated meetings ═══════════════════════════════════════

auth-meeting: $(SMOKE_STAMP)      ## authenticated join: S3 config, cookies, join type (60s)
	@$(T3)/tests/auth-meeting.sh

# ═══ Matrix validation (reads test-registry.yaml) ══════════════
# validate-<mode> runs every cheap-tier test registered for that mode,
# writes .state/reports/<mode>/<test>.json, exits non-zero on any failure.
# Reports drive the aggregator (Phase C+) and release-ship gate (Phase D).

validate-lite:                     ## run cheap-tier tests for lite mode + write JSON reports
	@$(T3)/lib/run-matrix.sh lite

validate-compose:                  ## run cheap-tier tests for compose mode + write JSON reports
	@$(T3)/lib/run-matrix.sh compose

validate-helm:                     ## run cheap-tier tests for helm mode + write JSON reports
	@$(T3)/lib/run-matrix.sh helm

report:                            ## aggregate .state/reports/* into tests3/reports/release-<tag>.md
	@mkdir -p $(ROOT)/tests3/reports
	@TAG=$$(cat $(STATE)/image_tag 2>/dev/null || echo "unknown"); \
	python3 $(T3)/lib/aggregate.py --out $(ROOT)/tests3/reports/release-$$TAG.md; \
	echo "  report → tests3/reports/release-$$TAG.md"

report-gate:                       ## like `report` but exits non-zero if any feature is below its confidence_min
	@mkdir -p $(ROOT)/tests3/reports
	@TAG=$$(cat $(STATE)/image_tag 2>/dev/null || echo "unknown"); \
	python3 $(T3)/lib/aggregate.py --out $(ROOT)/tests3/reports/release-$$TAG.md --gate-check

validate-all: validate-lite validate-compose validate-helm report  ## run all cheap tests on all modes + aggregate

# ═══ Composites ═══════════════════════════════════════════════════

e2e: meeting-tts                  ## smoke + full meeting (single script)
	@echo ""; echo "  E2E passed."

full: dashboard containers browser webhooks auth-meeting meeting-tts ## everything
	@echo ""; echo "  Full passed."

# ═══ VM — compose ═════════════════════════════════════════════════

vm-compose: locks vm-provision-compose vm-smoke-compose ## fresh VM + compose + smoke (~6min)
	@echo ""
	@echo "  ══════════════════════════════════════════"
	@echo "  VM compose validation passed."
	@echo "  Dashboard: http://$$(cat $(STATE)/vm_ip):3001"
	@echo "  SSH:       ssh root@$$(cat $(STATE)/vm_ip)"
	@echo "  Destroy:   make -C tests3 vm-destroy"
	@echo "  ══════════════════════════════════════════"

vm-provision-compose:              ## provision VM + deploy compose
	@$(T3)/lib/vm.sh provision compose
	@$(T3)/lib/vm.sh wait
	@$(T3)/lib/vm-setup-compose.sh

vm-smoke-compose:                  ## run the validate-compose matrix on VM (writes JSON reports)
	@$(T3)/lib/vm-run.sh validate-compose

# ═══ VM — lite ════════════════════════════════════════════════════

vm-lite: locks vm-provision-lite vm-smoke-lite ## fresh VM + lite + smoke (~5min)
	@echo ""
	@echo "  ══════════════════════════════════════════"
	@echo "  VM lite validation passed."
	@echo "  Dashboard: http://$$(cat $(STATE)/vm_ip):3000"
	@echo "  SSH:       ssh root@$$(cat $(STATE)/vm_ip)"
	@echo "  Destroy:   make -C tests3 vm-destroy"
	@echo "  ══════════════════════════════════════════"

vm-provision-lite:                 ## provision VM + deploy lite
	@$(T3)/lib/vm.sh provision lite
	@$(T3)/lib/vm.sh wait
	@$(T3)/lib/vm-setup-lite.sh

vm-smoke-lite:                     ## run the validate-lite matrix on VM (writes JSON reports)
	@$(T3)/lib/vm-run.sh validate-lite

# ═══ VM — shared ══════════════════════════════════════════════════

vm-destroy:                        ## tear down the VM
	@$(T3)/lib/vm.sh destroy

vm-ssh:                            ## open SSH to the VM
	@ssh -o StrictHostKeyChecking=no root@$$(cat $(STATE)/vm_ip)

vm-status:                         ## show VM state
	@echo "  VM_IP:    $$(cat $(STATE)/vm_ip 2>/dev/null || echo '-')"
	@echo "  VM_ID:    $$(cat $(STATE)/vm_id 2>/dev/null || echo '-')"
	@echo "  VM_MODE:  $$(cat $(STATE)/vm_mode 2>/dev/null || echo '-')"
	@echo "  VM_SETUP: $$(cat $(STATE)/vm_setup 2>/dev/null || echo '-')"

# ═══ Helm — staging ═══════════════════════════════════════════════

helm-check:                            ## verify kubectl can reach cluster
	@kubectl cluster-info > /dev/null 2>&1 || (echo "  ERROR: kubectl not configured" && exit 1)
	@echo "  $$(printf '\033[90m%s\033[0m' 'kubectl: connected')"

helm-smoke: helm-check smoke           ## smoke against helm (includes K8s checks)
	@echo ""; echo "  Helm smoke passed."

helm-dashboard: helm-smoke dashboard   ## dashboard tests against helm
	@echo ""

helm-containers: helm-smoke containers ## container lifecycle against helm
	@echo ""

helm-full: helm-check full             ## full suite against helm
	@echo ""; echo "  Helm full passed."

# ═══ LKE — provision + helm deploy + test ════════════════════════

lke-helm: locks lke-provision lke-setup lke-smoke ## fresh LKE cluster + helm + smoke
	@echo ""
	@echo "  ══════════════════════════════════════════"
	@echo "  LKE helm validation passed."
	@echo "  Gateway:   http://$$(cat $(STATE)/lke_node_ip):30056"
	@echo "  Dashboard: http://$$(cat $(STATE)/lke_node_ip):30001"
	@echo "  kubectl:   export KUBECONFIG=$$(cat $(STATE)/lke_kubeconfig_path)"
	@echo "  Destroy:   make -C tests3 lke-destroy"
	@echo "  ══════════════════════════════════════════"

lke-provision:                         ## provision LKE cluster + wait for nodes
	@$(T3)/lib/lke.sh provision
	@$(T3)/lib/lke.sh wait

lke-setup:                             ## deploy helm chart to LKE cluster
	@$(T3)/lib/lke-setup-helm.sh

lke-smoke:                             ## run the validate-helm matrix + write JSON reports on LKE
	@$(MAKE) --no-print-directory validate-helm DEPLOY_MODE=helm

lke-load-db:                           ## load production DB dump into cluster Postgres
	@$(T3)/lib/lke-load-db.sh $(DUMP_FILE)

lke-destroy:                           ## tear down LKE cluster
	@$(T3)/lib/lke.sh destroy

lke-status:                            ## show LKE cluster state
	@echo "  LKE_ID:     $$(cat $(STATE)/lke_id 2>/dev/null || echo '-')"
	@echo "  LKE_LABEL:  $$(cat $(STATE)/lke_label 2>/dev/null || echo '-')"
	@echo "  LKE_NODE:   $$(cat $(STATE)/lke_node_ip 2>/dev/null || echo '-')"
	@echo "  LKE_SETUP:  $$(cat $(STATE)/lke_setup 2>/dev/null || echo '-')"
	@echo "  KUBECONFIG: $$(cat $(STATE)/lke_kubeconfig_path 2>/dev/null || echo '-')"

# ═══ Data collection ══════════════════════════════════════════════

collect: $(SMOKE_STAMP)             ## collect: run live meeting, capture dataset (CONVERSATION=3speakers)
	@CONVERSATION=$${CONVERSATION:-3speakers} $(T3)/tests/collect.sh

score:                              ## score: re-score existing dataset offline (DATASET=gmeet-compose-260405)
	@DATASET=$${DATASET} $(T3)/tests/score.sh

# ═══ Util ═════════════════════════════════════════════════════════

what-changed:                      ## resolve changed files to test targets
	@TARGETS=$$(git diff --name-only $${BASE:-main} | python3 $(T3)/resolve.py); \
	if [ -n "$$TARGETS" ]; then \
		echo "→ make -C tests3 $$TARGETS"; \
	fi

clean:                             ## clear .state/ (keeps logs, credentials, infra state)
	@rm -f $(STATE)/.smoke-passed $(STATE)/.http_code $(STATE)/deploy_mode
	@echo "Smoke stamp cleared. Logs and credentials preserved."

clean-all:                         ## nuke all .state/ (destructive — loses logs, tokens, kubeconfig)
	@rm -f $(STATE)/*
	@echo "All state cleared."

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -ts$$'\t'
