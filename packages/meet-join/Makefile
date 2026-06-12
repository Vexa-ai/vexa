# Easy debug interface for the isolated joining layer.
#
#   make debug URL="https://meet.google.com/xxx-xxxx-xxx"
#   → builds the self-contained image, joins the meeting on a Linux display
#     with real humanized X11 input, and serves the live view at:
#       http://localhost:6080/vnc.html   (password: none)
IMAGE := meet-join-debug

.PHONY: build debug check
build:
	docker build -f Dockerfile.debug -t $(IMAGE) .

debug: build
	@echo ""
	@echo "  Watch live →  http://localhost:6080/vnc.html"
	@echo ""
	docker run --rm -it -p 6080:6080 -e MEETING_URL="$(URL)" $(IMAGE)

check:
	node scripts/check-isolation.js

# Same container, DIFFERENT network position — the egress IP is the only
# variable (#444: Google's gate keys on IP reputation). CLOUD_HOST is any
# ssh-able box:
#   bbb            → second household vantage (different ASN; NOT datacenter)
#   <linode-vm>    → production network position (throwaway VM via tests3
#                    provision/teardown; prod bots speak from Linode ranges)
#   make debug-cloud URL="https://meet.google.com/xxx-xxxx-xxx" CLOUD_HOST=<host>
CLOUD_HOST ?= bbb
BRANCH ?= $(shell git rev-parse --abbrev-ref HEAD)
debug-cloud:
	ssh $(CLOUD_HOST) 'rm -rf /tmp/meet-join-debug && git clone -q --depth 1 -b $(BRANCH) https://github.com/DmitriyG228/vexa /tmp/meet-join-debug'
	ssh $(CLOUD_HOST) 'cd /tmp/meet-join-debug/packages/meet-join && docker build -f Dockerfile.debug -t meet-join-debug . >/dev/null'
	@echo ""
	@echo "  Watch live (tunneled) →  http://localhost:6080/vnc.html"
	@echo ""
	ssh -t -L 6080:localhost:6080 $(CLOUD_HOST) 'docker run --rm -p 6080:6080 -e MEETING_URL="$(URL)" meet-join-debug'
