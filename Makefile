# =============================================================================
# Vexa open-core — top-level deploy entrypoint (Docker Compose)
# =============================================================================
.PHONY: all up down bot lite help release release-verify

help:
	@echo "Vexa deploy:"
	@echo "  make all   full Docker Compose stack"
	@echo "  make bot   build the meeting bot from source (needed before bots can join)"
	@echo "  make lite  single-container Vexa Lite from the published image"
	@echo "  make down  stop the compose stack"
	@echo "Release (maintainers — see deploy/release/RELEASE.md):"
	@echo "  make release TAG=vX.Y.Z         build+push the FULL image set, OCI-labelled"
	@echo "  make release-verify TAG=vX.Y.Z  gate:release-set — set complete at the tag?"

all up:              ## full compose stack
	@$(MAKE) --no-print-directory -C deploy/compose up

lite:                ## single-container Vexa Lite (provision + run + verify) — see deploy/lite
	@$(MAKE) --no-print-directory -C deploy/lite all

bot:                 ## build the meeting bot image from source (matches the stack's lifecycle.v1)
	@$(MAKE) --no-print-directory -C deploy/compose bot

down:                ## stop the compose stack
	@$(MAKE) --no-print-directory -C deploy/compose down

release:             ## build+push the whole release image set at TAG (deploy/release/RELEASE.md)
	@deploy/release/build-set.sh $(TAG)

release-verify:      ## gate:release-set — every image present at TAG with matching revision
	@deploy/release/verify-set.sh $(TAG)
