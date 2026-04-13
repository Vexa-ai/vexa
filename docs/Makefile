.PHONY: dev help

dev:                               ## start mintlify dev server on localhost:3333
	@command -v mintlify >/dev/null 2>&1 || { echo "Installing mintlify CLI..."; npm i -g mintlify; }
	mintlify dev --port 3333

help:                              ## show targets
	@grep -E '^[a-z].*:.*##' $(MAKEFILE_LIST) | awk -F '##' '{printf "  %-20s %s\n", $$1, $$2}'
