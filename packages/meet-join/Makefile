# Hot debug container for the isolated joining layer — runs the brick FROM SOURCE
# in a reproducible environment (Linux + Xvfb + humanized X11 + noVNC). Edit src/
# on the host, re-run, see it live at http://localhost:6080/vnc.html — no rebuild.
#
#   make debug URL="https://meet.google.com/xxx-xxxx-xxx"        # local egress
#   make debug-cloud URL="..." CLOUD_HOST=<host>                 # other egress IP
#
IMAGE := meet-join-debug
SRC_MOUNT := -v $(CURDIR)/src:/pkg/src -v $(CURDIR)/scripts:/pkg/scripts
CLOUD_HOST ?= bbb
BRANCH ?= $(shell git rev-parse --abbrev-ref HEAD)

.PHONY: debug image debug-cloud check

# Build the environment image once (deps + tools). Re-run only when deps change.
image:
	docker build -f Dockerfile.debug -t $(IMAGE) .

# Hot watch: build image if missing, then run with LIVE host source mounted.
# Source is executed via tsx — a host edit + `make debug` again is instant.
debug:
	@docker image inspect $(IMAGE) >/dev/null 2>&1 || $(MAKE) image
	@echo ""
	@echo "  Watch live →  http://localhost:6080/vnc.html"
	@echo ""
	docker run --rm -it -p 6080:6080 $(SRC_MOUNT) -e MEETING_URL="$(URL)" $(IMAGE)

# Same container, DIFFERENT network position — the egress IP is the only variable
# (#444: Google's gate keys on IP reputation). CLOUD_HOST is any ssh-able box:
#   bbb         → second household vantage (different ASN; NOT datacenter)
#   <linode-vm> → production network position (throwaway VM via tests3 provision)
# Source is rsync'd live so the cloud run is hot too (no commit needed).
debug-cloud:
	ssh $(CLOUD_HOST) 'mkdir -p /tmp/meet-join-debug'
	rsync -az --delete --exclude node_modules --exclude dist ./ $(CLOUD_HOST):/tmp/meet-join-debug/
	ssh $(CLOUD_HOST) 'cd /tmp/meet-join-debug && docker build -f Dockerfile.debug -t $(IMAGE) . >/dev/null'
	@echo ""
	@echo "  Watch live (tunneled) →  http://localhost:6080/vnc.html"
	@echo ""
	ssh -t -L 6080:localhost:6080 $(CLOUD_HOST) 'cd /tmp/meet-join-debug && docker run --rm -p 6080:6080 -v /tmp/meet-join-debug/src:/pkg/src -v /tmp/meet-join-debug/scripts:/pkg/scripts -e MEETING_URL="$(URL)" $(IMAGE)'

check:
	node scripts/check-isolation.js
