# Remote-browser login/validate harness — log into a platform once over VNC, persist
# the session, then validate it restores. Sessions land in ./profiles (gitignored —
# they are LIVE login cookies). The same profile dir is what the bot mounts/syncs to
# join authenticated.
#
#   make image                                  # build the harness (needs vexa/meet-join-env:dev)
#   make login PLATFORM=google                  # log in via http://localhost:6080/vnc.html
#   make watch                                  # follow the login container's logs
#   make validate PLATFORM=google               # confirm the saved session is still logged in
#
IMAGE := remote-browser-harness
ENV_IMAGE := vexa/meet-join-env:dev
PROFILES_DIR ?= $(CURDIR)/profiles
PLATFORM ?= google
SRC_MOUNT := -v $(CURDIR)/src:/pkg/src -v $(CURDIR)/scripts:/pkg/scripts -v $(PROFILES_DIR):/data/profiles

.PHONY: image login watch stop validate check build

image:
	@docker image inspect $(ENV_IMAGE) >/dev/null 2>&1 || { echo "Missing $(ENV_IMAGE). Build it: (cd ../join && make env)"; exit 1; }
	DOCKER_BUILDKIT=1 docker build -f Dockerfile.login -t $(IMAGE) .

# Detached so you interact via noVNC while it polls for your login. `make watch` to follow.
login:
	@docker image inspect $(IMAGE) >/dev/null 2>&1 || $(MAKE) image
	@mkdir -p $(PROFILES_DIR)
	@docker rm -f rb-login >/dev/null 2>&1 || true
	docker run -d --name rb-login -p 6080:6080 $(SRC_MOUNT) \
	  -e RB_CMD=login -e PLATFORM=$(PLATFORM) \
	  -e PROFILE=/data/profiles/$(PLATFORM) -e BACKUP=/data/profiles/$(PLATFORM)-backup \
	  $(IMAGE)
	@echo ""
	@echo "  Log in here →  http://localhost:6080/vnc.html"
	@echo "  Watch       →  make watch   (LOGIN RESULT prints when a session cookie is detected)"
	@echo ""

watch:
	docker logs -f rb-login

stop:
	docker rm -f rb-login rb-validate >/dev/null 2>&1 || true

# Validate is quick + non-interactive — runs attached, exits 0 if still logged in.
validate:
	@docker image inspect $(IMAGE) >/dev/null 2>&1 || $(MAKE) image
	@docker rm -f rb-validate >/dev/null 2>&1 || true
	docker run --rm --name rb-validate -p 6081:6080 $(SRC_MOUNT) \
	  -e RB_CMD=validate -e PLATFORM=$(PLATFORM) -e PROFILE=/data/profiles/$(PLATFORM) \
	  $(IMAGE)

check:
	node scripts/check-isolation.js

build:
	npm run build
