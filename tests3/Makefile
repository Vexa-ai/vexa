.PHONY: detect locks env health contracts smoke \
       dashboard-auth dashboard-proxy dashboard \
       containers browser-session browser-login browser \
       meeting bot admit transcribe finalize post-meeting meeting-chain \
       webhooks auth-meeting \
       e2e full \
       vm-compose vm-lite vm-provision vm-wait vm-setup-compose vm-setup-lite \
       vm-smoke vm-dashboard vm-containers vm-webhooks vm-full vm-destroy vm-ssh vm-status \
       clean help

SHELL := /bin/bash
ROOT  := $(shell git rev-parse --show-toplevel)
T3    := $(ROOT)/tests3
STATE := $(T3)/.state

DEPLOY_MODE   ?= auto
GATEWAY_URL   ?=
ADMIN_URL     ?=
DASHBOARD_URL ?=

export ROOT DEPLOY_MODE GATEWAY_URL ADMIN_URL DASHBOARD_URL STATE

# ═══ Checks (registry-driven, instant) ═══════════════════════════

detect:                           ## auto-detect deploy mode and URLs
	@$(T3)/lib/detect.sh

locks:                            ## static regression locks (0s)
	@$(T3)/checks/run --tier static

env: detect                       ## env var consistency (2s)
	@$(T3)/checks/run --tier env

health: detect                    ## service endpoints respond (5s)
	@$(T3)/checks/run --tier health

contracts: detect                 ## API behavior (15s)
	@$(T3)/checks/run --tier contract

smoke: locks env health contracts ## all checks, no meetings (30s)
	@echo ""; echo "  Smoke passed."

# ═══ Dashboard ════════════════════════════════════════════════════

dashboard-auth: smoke             ## login + identity + cookie (5s)
	@$(T3)/tests/dashboard-auth.sh

dashboard-proxy: dashboard-auth   ## proxy: meetings, pagination, transcript, bot create (15s)
	@$(T3)/tests/dashboard-proxy.sh

dashboard: dashboard-proxy        ## all dashboard tests

# ═══ Container lifecycle ══════════════════════════════════════════

containers: smoke                 ## create/stop/remove, timeout, orphans (60s)
	@$(T3)/tests/containers.sh

# ═══ Browser session ══════════════════════════════════════════════

browser-session: smoke            ## CDP, S3 roundtrip, auto-save (120s)
	@$(T3)/tests/browser-session.sh

browser-login: browser-session    ## [human] Google login persistence
	@$(T3)/tests/browser-login.sh

browser: browser-session          ## browser without human gate

# ═══ Meeting chain ════════════════════════════════════════════════

meeting: smoke                    ## create meeting via CDP (60s)
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

meeting-chain: post-meeting       ## full meeting lifecycle

# ═══ Webhooks ═════════════════════════════════════════════════════

webhooks: smoke                   ## envelope, HMAC, no secret leak (10s)
	@$(T3)/tests/webhooks.sh

# ═══ Authenticated meetings ═══════════════════════════════════════

auth-meeting: smoke               ## authenticated join: S3 config, cookies, join type (60s)
	@$(T3)/tests/auth-meeting.sh

# ═══ Composites (local) ══════════════════════════════════════════

e2e: meeting-chain                ## smoke + full meeting chain
	@echo ""; echo "  E2E passed."

full: dashboard containers browser webhooks auth-meeting meeting-chain ## everything
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

vm-smoke-compose:                  ## run smoke + dashboard + containers on VM (compose)
	@$(T3)/lib/vm-run.sh smoke
	@$(T3)/lib/vm-run.sh dashboard-auth
	@$(T3)/lib/vm-run.sh dashboard-proxy
	@$(T3)/lib/vm-run.sh containers
	@$(T3)/lib/vm-run.sh webhooks

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

vm-smoke-lite:                     ## run smoke + dashboard + containers on VM (lite)
	@$(T3)/lib/vm-run.sh smoke
	@$(T3)/lib/vm-run.sh dashboard-auth
	@$(T3)/lib/vm-run.sh containers

# ═══ VM — shared ══════════════════════════════════════════════════

vm-destroy:                        ## tear down the VM
	@$(T3)/lib/vm.sh destroy

vm-ssh:                            ## open SSH to the VM
	@ssh -o StrictHostKeyChecking=no root@$$(cat $(STATE)/vm_ip)

vm-status:                         ## show VM state
	@echo "  VM_IP:    $$(cat $(STATE)/vm_ip 2>/dev/null || echo 'none')"
	@echo "  VM_ID:    $$(cat $(STATE)/vm_id 2>/dev/null || echo 'none')"
	@echo "  VM_MODE:  $$(cat $(STATE)/vm_mode 2>/dev/null || echo 'none')"
	@echo "  VM_SETUP: $$(cat $(STATE)/vm_setup 2>/dev/null || echo 'none')"

# ═══ Util ═════════════════════════════════════════════════════════

clean:                             ## clear .state/
	@rm -f $(STATE)/*
	@echo "State cleared."

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -ts$$'\t'
