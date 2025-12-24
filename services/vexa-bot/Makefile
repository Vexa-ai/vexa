# Hot Dev Kit (local to vexa-bot)
IMAGE_NAME ?= vexa-bot:test
MEETING_URL ?=
DOCKER_NETWORK ?= vexa_dev_vexa_default
HOT_CHANNEL := bot_commands:meeting:999

.PHONY: build rebuild test publish publish-leave

# Build image + create local dist/ (one-time setup)
build:
	@echo "Building Docker image..."
	@docker build -t $(IMAGE_NAME) -f core/Dockerfile core
	@echo "Creating local dist/ for hot-reload..."
	@docker run --rm -v "$$(pwd)/core:/app" -w /app node:18 sh -c "npm install && npm run build"
	@echo "✅ Ready! Run: make test MEETING_URL=..."

# Rebuild dist/ when you edit code (fast, uses Docker)
rebuild:
	@docker run --rm -v "$$(pwd)/core:/app" -w /app node:18 sh -c "npm install && npm run build"
	@echo "✅ dist/ rebuilt. Restart bot to pick up changes."

# Usage: make test MEETING_URL='https://teams.live.com/meet/...'
test:
	@[ -n "$(MEETING_URL)" ] || (echo "MEETING_URL is required" && exit 1)
	@cd core/src/platforms && ./hot-debug.sh $(MEETING_URL)

# Usage: make publish DATA='{"action":"leave"}' (CHANNEL optional)
publish:
	@[ -n "$(DATA)" ] || (echo "DATA is required" && exit 1)
	docker run --rm --network $(DOCKER_NETWORK) redis:alpine \
	  redis-cli -h redis -p 6379 PUBLISH $${CHANNEL:-$(HOT_CHANNEL)} '$(DATA)'

# Usage: make publish-leave
publish-leave:
	@$(MAKE) publish DATA='{"action":"leave"}'

