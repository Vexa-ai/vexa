# =============================================================================
# Vexa open-core — top-level deploy entrypoint (Docker Compose)
# =============================================================================
.PHONY: all up down help

help:
	@echo "Vexa deploy:"
	@echo "  make all   full Docker Compose stack"
	@echo "  make down  stop the compose stack"

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down
