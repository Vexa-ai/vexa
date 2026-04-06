.PHONY: all env build build-bot-image up down ps logs test init-db restore-db check_docker publish promote-staging promote-latest help-tags setup-api-key

# --- Paths ---
ROOT := $(shell git rev-parse --show-toplevel)
COMPOSE_FILE := $(ROOT)/deploy/compose/docker-compose.yml
ENV_FILE := $(ROOT)/.env
ENV_EXAMPLE := $(ROOT)/deploy/env/env-example

LAST_TAG_FILE := $(ROOT)/deploy/compose/.last-tag

# --- Image tagging ---
# Every build gets an immutable VERSION-YYMMDD-HHMM tag.
# Mutable tags (dev, staging, latest) are pointers updated only during publish/promote.
VERSION := $(shell cat $(ROOT)/VERSION 2>/dev/null || echo "0.0.0")
IMAGE_TAG ?= $(VERSION)-$(shell date +%y%m%d-%H%M)
export IMAGE_TAG

# DockerHub namespace
DOCKERHUB_USER ?= vexaai

# All images that get built and published
IMAGES := api-gateway admin-api runtime-api meeting-api agent-api mcp dashboard tts-service vexa-lite
BOT_IMAGE := vexa-bot

-include $(ENV_FILE)

COMPOSE_CMD = docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE)

# ============================================================================
# Main targets
# ============================================================================

all: env build up init-db setup-api-key test
	@echo "Vexa is running. API: http://localhost:$${API_GATEWAY_HOST_PORT:-8056}/docs"

help-tags:
	@echo "Image Tagging Workflow"
	@echo ""
	@echo "  make build              - Build all images with timestamp tag (saved to .last-tag)"
	@echo "  make up                 - Start services using last-built tag"
	@echo "  make publish            - Push all images to DockerHub + update :dev pointer"
	@echo "  make promote-staging    - Set :staging tag to TAG= (or last published)"
	@echo "  make promote-latest     - Set :latest tag to TAG= (or last published)"
	@echo ""
	@echo "Current tag: $$(cat $(LAST_TAG_FILE) 2>/dev/null || echo '(none — run make build)')"
	@echo ""
	@echo "Examples:"
	@echo "  make build                         # builds all as vexaai/*:260330-1415"
	@echo "  make publish                       # pushes + sets :dev"
	@echo "  make promote-staging TAG=260330-1415"

# --- Environment ---
env:
	@if [ ! -f $(ENV_FILE) ]; then \
		cp $(ENV_EXAMPLE) $(ENV_FILE); \
		echo "Created .env from env-example. Edit $(ENV_FILE) before running."; \
		echo "  - Set TRANSCRIPTION_SERVICE_URL to your transcription endpoint"; \
		echo "  - Set TRANSCRIPTION_SERVICE_TOKEN"; \
	else \
		echo ".env already exists. Delete it to regenerate from env-example."; \
	fi

# --- Docker check ---
check_docker:
	@if ! docker info > /dev/null 2>&1; then \
		echo "ERROR: Docker is not running."; \
		exit 1; \
	fi

# --- Build ---
build-bot-image: check_docker
	@echo "Building $(DOCKERHUB_USER)/$(BOT_IMAGE):$(IMAGE_TAG)..."
	docker build --platform linux/amd64 -t $(DOCKERHUB_USER)/$(BOT_IMAGE):$(IMAGE_TAG) -f $(ROOT)/services/vexa-bot/Dockerfile $(ROOT)/services/vexa-bot

build-lite: check_docker
	@echo "Building $(DOCKERHUB_USER)/vexa-lite:$(IMAGE_TAG)..."
	docker build --platform linux/amd64 -t $(DOCKERHUB_USER)/vexa-lite:$(IMAGE_TAG) -f $(ROOT)/deploy/lite/Dockerfile.lite $(ROOT)

build: check_docker build-bot-image build-lite
	@echo "Building all images with tag: $(IMAGE_TAG)"
	$(COMPOSE_CMD) build
	@echo "$(IMAGE_TAG)" > $(LAST_TAG_FILE)
	@echo ""
	@echo "All images tagged: $(IMAGE_TAG)"
	@echo "Tag saved to $(LAST_TAG_FILE)"
	@docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}' | grep "$(IMAGE_TAG)" || true

# --- Up / Down ---
up: check_docker
	@if [ -f $(LAST_TAG_FILE) ]; then \
		export IMAGE_TAG=$$(cat $(LAST_TAG_FILE)); \
		echo "Using image tag: $$IMAGE_TAG (from .last-tag)"; \
	else \
		echo "No .last-tag found — using IMAGE_TAG=$(IMAGE_TAG)"; \
	fi
	@if ! docker network ls | grep -q "vexa-network"; then \
		docker network create vexa-network || true; \
	fi
	$(COMPOSE_CMD) up -d
	@if grep -qE '^LOCAL_TRANSCRIPTION=true' $(ENV_FILE) 2>/dev/null; then \
		echo "LOCAL_TRANSCRIPTION=true — starting transcription-service..."; \
		cd $(ROOT)/services/transcription-service && docker compose up -d; \
	fi

down: check_docker
	$(COMPOSE_CMD) down
	@cd $(ROOT)/services/transcription-service && docker compose down 2>/dev/null || true

# --- Service API key (dashboard WS auth) ---
# Creates a default user (if needed) and a bot+browser+tx API key, writes VEXA_API_KEY to .env.
# The dashboard uses this for WebSocket auth and transcript reads.
setup-api-key: check_docker
	@if grep -qE '^VEXA_API_KEY=.+' $(ENV_FILE) 2>/dev/null; then \
		echo "VEXA_API_KEY already set in .env — skipping."; \
	else \
		API_PORT=$$(grep -E '^API_GATEWAY_HOST_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
		API_PORT=$${API_PORT:-8056}; \
		ADMIN_TK=$$(grep -E '^ADMIN_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
		ADMIN_TK=$${ADMIN_TK:-changeme}; \
		USER_ID=$$(curl -sf "http://localhost:$$API_PORT/admin/users?limit=1" \
			-H "X-Admin-API-Key: $$ADMIN_TK" 2>/dev/null \
			| python3 -c "import sys,json; u=json.load(sys.stdin); print(u[0]['id'] if u else '')" 2>/dev/null); \
		if [ -z "$$USER_ID" ]; then \
			echo "No users found — creating default user..."; \
			USER_ID=$$(curl -sf -X POST "http://localhost:$$API_PORT/admin/users" \
				-H "X-Admin-API-Key: $$ADMIN_TK" -H "Content-Type: application/json" \
				-d '{"email":"admin@vexa.ai","name":"Admin","max_concurrent_bots":10}' 2>/dev/null \
				| python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null); \
		fi; \
		if [ -z "$$USER_ID" ]; then \
			echo "WARNING: Could not find or create user (admin API not ready?). Dashboard WS will require login."; \
		else \
			TOKEN=$$(curl -sf -X POST "http://localhost:$$API_PORT/admin/users/$$USER_ID/tokens?scopes=bot,browser,tx&name=dashboard-ws" \
				-H "X-Admin-API-Key: $$ADMIN_TK" 2>/dev/null \
				| python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null); \
			if [ -n "$$TOKEN" ]; then \
				echo "VEXA_API_KEY=$$TOKEN" >> $(ENV_FILE); \
				echo "Created VEXA_API_KEY for user $$USER_ID (dashboard WS auth)."; \
				$(COMPOSE_CMD) up -d dashboard; \
			else \
				echo "WARNING: Could not create API key for user $$USER_ID. Dashboard WS will require login."; \
			fi; \
		fi; \
	fi

# --- Status ---
ps: check_docker
	$(COMPOSE_CMD) ps

logs:
	$(COMPOSE_CMD) logs -f

# --- Test ---
test: check_docker
	@echo "Waiting for services to be ready..."
	@DASH_PORT=$$(grep -E '^DASHBOARD_HOST_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "3001"); \
	DASH_PORT=$${DASH_PORT:-3001}; \
	for i in 1 2 3 4 5; do \
		curl -sf "http://localhost:$$DASH_PORT/" > /dev/null 2>&1 && break; \
		sleep 2; \
	done
	@echo "Checking services..."
	@API_PORT=$$(grep -E '^API_GATEWAY_HOST_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "8056"); \
	ADMIN_PORT=$$(grep -E '^ADMIN_API_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "8057"); \
	DASH_PORT=$$(grep -E '^DASHBOARD_HOST_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "3001"); \
	API_PORT=$${API_PORT:-8056}; ADMIN_PORT=$${ADMIN_PORT:-8057}; DASH_PORT=$${DASH_PORT:-3001}; \
	if curl -sf "http://localhost:$$API_PORT/" > /dev/null 2>&1; then \
		echo "  ✓ API Gateway (port $$API_PORT)"; \
	else \
		echo "  ✗ API Gateway not responding (port $$API_PORT)"; \
	fi; \
	if curl -sf "http://localhost:$$ADMIN_PORT/" > /dev/null 2>&1; then \
		echo "  ✓ Admin API (port $$ADMIN_PORT)"; \
	else \
		echo "  ✗ Admin API not responding (port $$ADMIN_PORT)"; \
	fi; \
	if curl -sf "http://localhost:$$DASH_PORT/" > /dev/null 2>&1; then \
		echo "  ✓ Dashboard (port $$DASH_PORT)"; \
	else \
		echo "  ✗ Dashboard not responding (port $$DASH_PORT)"; \
	fi; \
	echo ""; \
	echo "Dashboard: http://localhost:$$DASH_PORT"; \
	echo "API docs:  http://localhost:$$API_PORT/docs"; \
	echo "Admin:     http://localhost:$$ADMIN_PORT/docs"; \
	echo ""; \
	echo "Image tag: $$(cat $(LAST_TAG_FILE) 2>/dev/null || echo 'unknown')"; \
	echo "To test: open the dashboard and join a Google Meet."

# ============================================================================
# Publishing & Promotion
# ============================================================================

# Push all images to DockerHub with immutable tag, then update :dev pointer
publish: check_docker
	@if [ ! -f $(LAST_TAG_FILE) ]; then \
		echo "ERROR: No .last-tag found. Run 'make build' first."; exit 1; \
	fi
	@TAG=$$(cat $(LAST_TAG_FILE)); \
	echo "Publishing all images with tag: $$TAG"; \
	for img in $(IMAGES); do \
		echo "  Pushing $(DOCKERHUB_USER)/$$img:$$TAG..."; \
		docker push $(DOCKERHUB_USER)/$$img:$$TAG; \
		echo "  Updating $(DOCKERHUB_USER)/$$img:dev → $$TAG"; \
		docker buildx imagetools create -t $(DOCKERHUB_USER)/$$img:dev $(DOCKERHUB_USER)/$$img:$$TAG; \
	done; \
	echo "  Pushing $(DOCKERHUB_USER)/$(BOT_IMAGE):$$TAG..."; \
	docker push $(DOCKERHUB_USER)/$(BOT_IMAGE):$$TAG; \
	echo "  Updating $(DOCKERHUB_USER)/$(BOT_IMAGE):dev → $$TAG"; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(BOT_IMAGE):dev $(DOCKERHUB_USER)/$(BOT_IMAGE):$$TAG; \
	echo ""; \
	echo "Published $$TAG. :dev pointers updated."

# Promote a tag to :staging
promote-staging: check_docker
	@TAG_TO_USE="$(TAG)"; \
	if [ -z "$$TAG_TO_USE" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "ERROR: No TAG specified and .last-tag not found."; exit 1; \
		fi; \
	fi; \
	echo "Promoting $$TAG_TO_USE → :staging"; \
	for img in $(IMAGES) $(BOT_IMAGE); do \
		echo "  $(DOCKERHUB_USER)/$$img:staging → $$TAG_TO_USE"; \
		docker buildx imagetools create -t $(DOCKERHUB_USER)/$$img:staging $(DOCKERHUB_USER)/$$img:$$TAG_TO_USE; \
	done; \
	echo "Done. :staging now points to $$TAG_TO_USE"

# Promote a tag to :latest
promote-latest: check_docker
	@TAG_TO_USE="$(TAG)"; \
	if [ -z "$$TAG_TO_USE" ]; then \
		if [ -f $(LAST_TAG_FILE) ]; then \
			TAG_TO_USE=$$(cat $(LAST_TAG_FILE)); \
		else \
			echo "ERROR: No TAG specified and .last-tag not found."; exit 1; \
		fi; \
	fi; \
	echo "Promoting $$TAG_TO_USE → :latest"; \
	for img in $(IMAGES) $(BOT_IMAGE); do \
		echo "  $(DOCKERHUB_USER)/$$img:latest → $$TAG_TO_USE"; \
		docker buildx imagetools create -t $(DOCKERHUB_USER)/$$img:latest $(DOCKERHUB_USER)/$$img:$$TAG_TO_USE; \
	done; \
	echo "Done. :latest now points to $$TAG_TO_USE"

# ============================================================================
# Database schema sync (idempotent — no Alembic, no migration versions)
# ============================================================================

init-db: check_docker
	@set -e; \
	if [ "$(REMOTE_DB)" != "true" ]; then \
		if ! $(COMPOSE_CMD) ps -q postgres | grep -q .; then \
			echo "ERROR: postgres not running. Run 'make up' first."; exit 1; \
		fi; \
		count=0; \
		DB_USER=$$(grep -E '^DB_USER=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "postgres"); \
		DB_NAME=$$(grep -E '^DB_NAME=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "vexa"); \
		DB_USER=$${DB_USER:-postgres}; DB_NAME=$${DB_NAME:-vexa}; \
		while ! $(COMPOSE_CMD) exec -T postgres pg_isready -U $$DB_USER -d $$DB_NAME -q; do \
			if [ $$count -ge 12 ]; then echo "ERROR: DB not ready in 60s."; exit 1; fi; \
			sleep 5; count=$$((count+1)); \
		done; \
	fi; \
	echo "Running idempotent schema sync..."; \
	$(COMPOSE_CMD) exec -T admin-api python -c "import asyncio; from admin_models.database import init_db; asyncio.run(init_db())"; \
	$(COMPOSE_CMD) exec -T meeting-api python -c "import asyncio; from meeting_api.database import init_db; asyncio.run(init_db())"; \
	echo "Schema sync complete."

# Restore a production dump into the local postgres.
# Extracts only the public schema, loads data, then runs schema sync.
#   make restore-db DUMP=~/secrets/production-dump.sql
restore-db: check_docker
	@if [ -z "$(DUMP)" ]; then \
		echo "Usage: make restore-db DUMP=<path-to-dump.sql>"; exit 1; \
	fi
	$(ROOT)/tests/scripts/restore-prod-dump.sh "$(DUMP)"
