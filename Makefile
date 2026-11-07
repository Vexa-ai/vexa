# =============================================================================
# Vexa open-core — top-level deploy entrypoint (Docker Compose)
# =============================================================================
.PHONY: all up dev down bot lite probe bank-profile login help

SURFACE ?= compose

help:
	@echo "Vexa deploy:"
	@echo "  make all   full Docker Compose stack from the PUBLISHED images (bot included — pulled)"
	@echo "  make dev   full stack built from THIS checkout, tagged :dev (contributors)"
	@echo "  make bot   build the meeting bot from source into vexa/vexa-bot:dev (dev path)"
	@echo "  make lite  single-container Vexa Lite from the published image"
	@echo "  make probe full-journey smoke probe of a RUNNING install (SURFACE=compose|lite|helm)"
	@echo "  make bank-profile the restricted reference environment, a release gate (IMAGE_TAG=<tag>; bbb-sized)"
	@echo "  make login provision an authenticated-bot session (sign in once; uploads to userdata storage)"
	@echo "  make down  stop the compose stack"

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

lite:                ## single-container Vexa Lite (provision + run + verify) — see deploy/lite
	@$(MAKE) --no-print-directory -C deploy/lite all

dev:                 ## full stack built from this checkout (:dev tags — never shadows published v012)
	@$(MAKE) --no-print-directory -C deploy/compose dev

bot:                 ## build the meeting bot from source → vexa/vexa-bot:dev (dev path; install pulls the published bot)
	@$(MAKE) --no-print-directory -C deploy/compose bot

probe:               ## full-journey smoke probe (spawn→…→stop + log sweep) of a running install — see deploy/$(SURFACE)/probe.sh
	@./deploy/$(SURFACE)/probe.sh

bank-profile:        ## bank-ready gate: kind + Calico + PSA restricted/restricted-v2 + egress allowlist + corp CA + OIDC — see deploy/bank-profile
	@./deploy/bank-profile/run.sh all

login:               ## provision an authenticated-bot session — sign in once, persist it (docs: /authenticated-bots)
	@pnpm --filter @vexa/remote-browser login

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down
