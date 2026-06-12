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
