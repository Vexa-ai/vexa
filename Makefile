# =============================================================================
# Vexa open-core — top-level deploy entrypoint (Docker Compose)
# =============================================================================
.PHONY: all up down bot lite help

help:
	@echo "Vexa deploy:"
	@echo "  make all   full Docker Compose stack"
	@echo "  make bot   build the meeting bot from source (needed before bots can join)"
	@echo "  make lite  single-container Vexa Lite from the published image"
	@echo "  make down  stop the compose stack"

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

lite:                ## single-container Vexa Lite (provision + run + verify) — see deploy/lite
	@$(MAKE) --no-print-directory -C deploy/lite all

bot:                 ## build the meeting bot image from source (matches the stack's lifecycle.v1)
	@$(MAKE) --no-print-directory -C deploy/compose bot

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down
