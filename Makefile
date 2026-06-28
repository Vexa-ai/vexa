# =============================================================================
# Vexa (v0.12) — top-level deploy entrypoints
# =============================================================================
# Two self-host shapes (docs/roadmap/deployment.mdx):
#   make lite   — single container, all services (process backend) — quick eval / small teams
#   make all    — full stack, each service in its own container (compose) — dev / production
.PHONY: lite lite-down all up down help

help:
	@echo "Vexa deploy:"
	@echo "  make lite        single-container deploy (Vexa Lite)"
	@echo "  make lite-down   stop the lite container + sidecars"
	@echo "  make all         full Docker Compose stack"
	@echo "  make down        stop the compose stack"

lite:                ## single-container deploy (Vexa Lite)
	@$(MAKE) --no-print-directory -C deploy/lite all

lite-down:           ## stop lite container + sidecars
	@$(MAKE) --no-print-directory -C deploy/lite down

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down
