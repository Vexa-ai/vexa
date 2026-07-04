# =============================================================================
# Vexa open-core — top-level deploy entrypoint (Docker Compose)
# =============================================================================
.PHONY: all up down bot help

help:
	@echo "Vexa deploy:"
	@echo "  make all   full Docker Compose stack"
	@echo "  make bot   build the meeting bot from source (needed before bots can join)"
	@echo "  make down  stop the compose stack"

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

bot:                 ## build the meeting bot image from source (matches the stack's lifecycle.v1)
	@$(MAKE) --no-print-directory -C deploy/compose bot

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down
