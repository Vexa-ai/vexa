# =============================================================================
# Vexa open-core — top-level deploy entrypoint (Docker Compose)
# =============================================================================
.PHONY: all up dev down bot lite help

help:
	@echo "Vexa deploy:"
	@echo "  make all   full Docker Compose stack from the PUBLISHED images (bot included — pulled)"
	@echo "  make dev   full stack built from THIS checkout, tagged :dev (contributors)"
	@echo "  make bot   build the meeting bot from source into vexa/vexa-bot:dev (dev path)"
	@echo "  make lite  single-container Vexa Lite from the published image"
	@echo "  make down  stop the compose stack"

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

lite:                ## single-container Vexa Lite (provision + run + verify) — see deploy/lite
	@$(MAKE) --no-print-directory -C deploy/lite all

dev:                 ## full stack built from this checkout (:dev tags — never shadows published v012)
	@$(MAKE) --no-print-directory -C deploy/compose dev

bot:                 ## build the meeting bot from source → vexa/vexa-bot:dev (dev path; install pulls the published bot)
	@$(MAKE) --no-print-directory -C deploy/compose bot

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down
