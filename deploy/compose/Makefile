.PHONY: all all-build env build build-bot-image build-transcript-rendering up down ps logs test test-transcription init-db restore-db check_docker preflight publish promote-staging promote-latest help-tags setup-api-key

# --- Paths ---
ROOT := $(shell git rev-parse --show-toplevel)
COMPOSE_FILE := $(ROOT)/deploy/compose/docker-compose.yml
ENV_FILE := $(ROOT)/.env
ENV_EXAMPLE := $(ROOT)/deploy/env-example

LAST_TAG_FILE := $(ROOT)/deploy/compose/.last-tag

# --- Image tagging ---
# BUILD_TAG: always a fresh timestamp for `make build`. Never from .env.
# IMAGE_TAG: what `make up` runs. Comes from .env (default: dev) or .last-tag after build.
VERSION := $(shell cat $(ROOT)/VERSION 2>/dev/null || echo "0.0.0")
BUILD_TAG := $(VERSION)-$(shell date +%y%m%d-%H%M)

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

all: env preflight up init-db setup-api-key test
	@HOST_IP=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	HOST=$${HOST_IP:-localhost}; \
	echo "Vexa is running. API: http://$$HOST:$${API_GATEWAY_HOST_PORT:-8056}/docs"

all-build: env preflight build up init-db setup-api-key test
	@HOST_IP=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	HOST=$${HOST_IP:-localhost}; \
	echo "Vexa is running (built from source). API: http://$$HOST:$${API_GATEWAY_HOST_PORT:-8056}/docs"

help-tags:
	@echo "Image Tagging Workflow"
	@echo ""
	@echo "  make build              - Build all images with fresh timestamp tag (saved to .last-tag)"
	@echo "  make up                 - Start services (uses .last-tag if built, else IMAGE_TAG from .env)"
	@echo "  make publish            - Push all images to DockerHub + update :dev pointer"
	@echo "  make promote-staging    - Set :staging tag to TAG= (or last published)"
	@echo "  make promote-latest     - Set :latest tag to TAG= (or last published)"
	@echo ""
	@echo "Last built: $$(cat $(LAST_TAG_FILE) 2>/dev/null || echo '(none — using IMAGE_TAG from .env)')"
	@echo "IMAGE_TAG in .env: $(IMAGE_TAG)"
	@echo ""
	@echo "Examples:"
	@echo "  make all                           # pulls IMAGE_TAG from .env (default: dev)"
	@echo "  make all-build                     # builds fresh + starts"
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
		added=0; \
		while IFS= read -r line; do \
			key=$$(echo "$$line" | sed -n 's/^#*[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); \
			if [ -n "$$key" ] && ! grep -qE "^#*\s*$$key=" $(ENV_FILE); then \
				echo "$$line" >> $(ENV_FILE); \
				added=$$((added+1)); \
			fi; \
		done < $(ENV_EXAMPLE); \
		if [ $$added -gt 0 ]; then \
			echo ".env patched: $$added new variable(s) added from env-example."; \
		else \
			echo ".env is up to date."; \
		fi; \
	fi
	@if ! grep -qE '^DOCKER_GID=' $(ENV_FILE) 2>/dev/null; then \
		GID=$$(getent group docker 2>/dev/null | cut -d: -f3); \
		if [ -n "$$GID" ]; then \
			echo "DOCKER_GID=$$GID" >> $(ENV_FILE); \
			echo "Auto-detected DOCKER_GID=$$GID (written to .env)"; \
		fi; \
	fi
	@BRANCH=$$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main"); \
	if [ "$$BRANCH" = "main" ]; then TAG=latest; else TAG=dev; fi; \
	CURRENT=$$(grep -E '^IMAGE_TAG=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	if [ "$$CURRENT" != "$$TAG" ]; then \
		sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$$TAG|" $(ENV_FILE); \
		sed -i "s|^BROWSER_IMAGE=.*|BROWSER_IMAGE=vexaai/vexa-bot:$$TAG|" $(ENV_FILE); \
		echo "Auto-set IMAGE_TAG=$$TAG (branch=$$BRANCH)"; \
	fi

# --- Preflight — validate credentials before starting anything ---
preflight:
	@TX_URL=$$(grep -E '^TRANSCRIPTION_SERVICE_URL=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	TX_TOKEN=$$(grep -E '^TRANSCRIPTION_SERVICE_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	if [ -z "$$TX_TOKEN" ] || [ "$$TX_TOKEN" = "your-token" ]; then \
		echo ""; \
		echo "  Vexa uses transcription.vexa.ai to process meeting audio."; \
		echo "  If you use a different provider, edit .env and re-run make all."; \
		echo ""; \
		printf "  Press Enter to continue: "; \
		read -r CONFIRM < /dev/tty; \
		echo ""; \
		echo "  Get your token at: https://vexa.ai/account"; \
		echo ""; \
		printf "  Paste your transcription token: "; \
		read -r TX_TOKEN < /dev/tty; \
		echo ""; \
		if [ -z "$$TX_TOKEN" ]; then \
			echo "  ✗ No token provided. Run 'make all' again when you have one."; exit 1; \
		fi; \
		sed -i "s|^TRANSCRIPTION_SERVICE_TOKEN=.*|TRANSCRIPTION_SERVICE_TOKEN=$$TX_TOKEN|" $(ENV_FILE); \
		echo "  ✓ Token saved to .env"; \
	fi; \
	if [ -z "$$TX_URL" ]; then \
		echo "  ✗ TRANSCRIPTION_SERVICE_URL not set in .env"; exit 1; \
	fi; \
	if [ -z "$$TX_TOKEN" ]; then \
		echo "  ✗ TRANSCRIPTION_SERVICE_TOKEN not set in .env"; exit 1; \
	fi; \
	HTTP=$$(curl -s -o /dev/null -w '%{http_code}' -X POST "$$TX_URL" \
		-H "Authorization: Bearer $$TX_TOKEN" \
		-F "file=@$(ROOT)/tests3/testdata/test-speech-en.wav" \
		-F "language=en" -F "model=large-v3-turbo" --max-time 10 2>/dev/null); \
	if [ "$$HTTP" = "200" ]; then \
		echo "  ✓ Transcription token valid"; \
	elif [ "$$HTTP" = "403" ]; then \
		echo "  ✗ TRANSCRIPTION_SERVICE_TOKEN rejected (403). Token expired — get a new one."; exit 1; \
	elif [ "$$HTTP" = "000" ]; then \
		echo "  ⚠ Transcription unreachable ($$TX_URL). Continuing..."; \
	else \
		echo "  ⚠ Transcription HTTP $$HTTP. Continuing..."; \
	fi

# --- Docker check ---
check_docker:
	@if ! docker info > /dev/null 2>&1; then \
		echo "ERROR: Docker is not running."; \
		exit 1; \
	fi

# --- Build ---
build-bot-image: check_docker
	@echo "Building $(DOCKERHUB_USER)/$(BOT_IMAGE):$(BUILD_TAG)..."
	docker build --platform linux/amd64 -t $(DOCKERHUB_USER)/$(BOT_IMAGE):$(BUILD_TAG) -f $(ROOT)/services/vexa-bot/Dockerfile $(ROOT)/services/vexa-bot

build-lite: check_docker
	@echo "Building $(DOCKERHUB_USER)/vexa-lite:$(BUILD_TAG)..."
	docker build --platform linux/amd64 -t $(DOCKERHUB_USER)/vexa-lite:$(BUILD_TAG) -f $(ROOT)/deploy/lite/Dockerfile.lite $(ROOT)

build-transcript-rendering:
	@echo "Building transcript-rendering package..."
	@cd $(ROOT)/packages/transcript-rendering && npm install && npm run build

build: check_docker build-bot-image build-transcript-rendering build-lite
	@echo "Building all images with tag: $(BUILD_TAG)"
	IMAGE_TAG=$(BUILD_TAG) $(COMPOSE_CMD) build
	@echo "$(BUILD_TAG)" > $(LAST_TAG_FILE)
	@echo ""
	@echo "All images tagged: $(BUILD_TAG)"
	@echo "Tag saved to $(LAST_TAG_FILE)"
	@docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}' | grep "$(BUILD_TAG)" || true

# --- Up / Down ---
# IMAGE_TAG comes from .env (default: dev = pre-built from DockerHub).
# `make build` writes a local tag to .last-tag which overrides .env.
up: check_docker preflight
	@if [ -f $(LAST_TAG_FILE) ]; then \
		export IMAGE_TAG=$$(cat $(LAST_TAG_FILE)); \
		echo "Using locally-built images (IMAGE_TAG=$$IMAGE_TAG from .last-tag)"; \
	else \
		export IMAGE_TAG=$$(grep -E '^IMAGE_TAG=' $(ENV_FILE) 2>/dev/null | cut -d= -f2 || echo "dev"); \
		echo "Using images (IMAGE_TAG=$$IMAGE_TAG from .env)"; \
	fi; \
	if ! docker network ls | grep -q "vexa-network"; then \
		docker network create vexa-network || true; \
	fi; \
	$(COMPOSE_CMD) up -d --pull=missing; \
	BOT_TAG=$$(cat $(LAST_TAG_FILE) 2>/dev/null || echo "$(IMAGE_TAG)"); \
	BOT_IMG=$(DOCKERHUB_USER)/$(BOT_IMAGE):$$BOT_TAG; \
	if ! docker image inspect $$BOT_IMG > /dev/null 2>&1; then \
		echo "Pulling bot image: $$BOT_IMG..."; \
		docker pull $$BOT_IMG; \
	fi; \
	if grep -qE '^LOCAL_TRANSCRIPTION=true' $(ENV_FILE) 2>/dev/null; then \
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
	@# v0.10.5 R3 fix (2026-04-27): validate existing VEXA_API_KEY before
	@# skipping. The earlier "skip-if-set" default left stale tokens in
	@# .env after redeploy when admin-api re-seeded credentials, causing
	@# dashboard 401 from /bots/status (DASHBOARD_API_KEY_VALID gate red).
	@# Now: if a key is present we PROBE the gateway with it; on 200 keep,
	@# on anything else drop+regenerate so dashboard is always seeded with
	@# a token the gateway accepts.
	@API_PORT=$$(grep -E '^API_GATEWAY_HOST_PORT=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	API_PORT=$${API_PORT:-8056}; \
	EXISTING_KEY=$$(grep -E '^VEXA_API_KEY=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	if [ -n "$$EXISTING_KEY" ]; then \
		HTTP=$$(curl -s -o /dev/null -w '%{http_code}' \
			-H "X-API-Key: $$EXISTING_KEY" \
			"http://localhost:$$API_PORT/bots/status" 2>/dev/null || echo "000"); \
		if [ "$$HTTP" = "200" ] || [ "$$HTTP" = "304" ]; then \
			echo "VEXA_API_KEY in .env validates against gateway (HTTP $$HTTP) — keeping."; \
			exit 0; \
		fi; \
		echo "VEXA_API_KEY in .env is stale (gateway returned HTTP $$HTTP) — regenerating."; \
		sed -i.bak '/^VEXA_API_KEY=/d' $(ENV_FILE) && rm -f $(ENV_FILE).bak; \
	fi; \
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
			if [ -f $(LAST_TAG_FILE) ]; then \
				export IMAGE_TAG=$$(cat $(LAST_TAG_FILE)); \
			else \
				export IMAGE_TAG=dev; \
			fi; \
			$(COMPOSE_CMD) up -d dashboard; \
		else \
			echo "WARNING: Could not create API key for user $$USER_ID. Dashboard WS will require login."; \
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
	HOST_IP=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	if [ -n "$$HOST_IP" ] && [ "$$HOST_IP" != "127.0.0.1" ]; then \
		echo "Dashboard: http://$$HOST_IP:$$DASH_PORT"; \
		echo "API docs:  http://$$HOST_IP:$$API_PORT/docs"; \
		echo "Admin:     http://$$HOST_IP:$$ADMIN_PORT/docs"; \
	else \
		echo "Dashboard: http://localhost:$$DASH_PORT"; \
		echo "API docs:  http://localhost:$$API_PORT/docs"; \
		echo "Admin:     http://localhost:$$ADMIN_PORT/docs"; \
	fi; \
	echo ""; \
	echo "Image tag: $$(cat $(LAST_TAG_FILE) 2>/dev/null || echo 'unknown')"; \
	echo "To test: open the dashboard and join a Google Meet."
	@$(MAKE) --no-print-directory test-transcription

# --- Transcription test ---
# Sends a real audio file to the transcription service and verifies text comes back.
# Catches: wrong URL, wrong token, service down, model mismatch.
TEST_AUDIO := $(ROOT)/tests3/testdata/test-speech-en.wav

test-transcription:
	@TX_URL=$$(grep -E '^TRANSCRIPTION_SERVICE_URL=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	TX_TOKEN=$$(grep -E '^TRANSCRIPTION_SERVICE_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	if [ -z "$$TX_URL" ]; then \
		echo "  ✗ Transcription: TRANSCRIPTION_SERVICE_URL not set in .env"; \
		exit 1; \
	fi; \
	if [ ! -f "$(TEST_AUDIO)" ]; then \
		echo "  ✗ Transcription: test audio file not found at $(TEST_AUDIO)"; \
		exit 1; \
	fi; \
	echo "Testing transcription at $$TX_URL ..."; \
	RESP=$$(curl -s -X POST "$$TX_URL" \
		-H "Authorization: Bearer $$TX_TOKEN" \
		-F "file=@$(TEST_AUDIO)" \
		-F "language=en" \
		-F "model=large-v3-turbo" \
		-w "\n%{http_code}" 2>&1); \
	HTTP=$$(echo "$$RESP" | tail -1); \
	BODY=$$(echo "$$RESP" | head -n -1); \
	if [ "$$HTTP" = "200" ]; then \
		TEXT=$$(echo "$$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text','')[:80])" 2>/dev/null); \
		if [ -n "$$TEXT" ]; then \
			echo "  ✓ Transcription works: \"$$TEXT\""; \
		else \
			echo "  ✗ Transcription returned 200 but no text"; \
			exit 1; \
		fi; \
	elif [ "$$HTTP" = "401" ] || [ "$$HTTP" = "403" ]; then \
		echo "  ✗ Transcription auth failed ($$HTTP). Check TRANSCRIPTION_SERVICE_TOKEN in .env"; \
		exit 1; \
	else \
		echo "  ✗ Transcription failed (HTTP $$HTTP)"; \
		echo "    $$BODY" | head -3; \
		exit 1; \
	fi

# ============================================================================
# Publishing & Promotion
# ============================================================================

# Push all images to DockerHub with immutable tag, then tag as :dev
# Uses docker tag + push (same manifest/SHA) instead of imagetools create (wraps in manifest list)
publish: check_docker
	@if [ ! -f $(LAST_TAG_FILE) ]; then \
		echo "ERROR: No .last-tag found. Run 'make build' first."; exit 1; \
	fi
	@TAG=$$(cat $(LAST_TAG_FILE)); \
	echo "Publishing all images with tag: $$TAG"; \
	for img in $(IMAGES) $(BOT_IMAGE); do \
		echo "  Pushing $(DOCKERHUB_USER)/$$img:$$TAG..."; \
		docker push $(DOCKERHUB_USER)/$$img:$$TAG; \
		echo "  Tagging $(DOCKERHUB_USER)/$$img:dev → $$TAG"; \
		docker tag $(DOCKERHUB_USER)/$$img:$$TAG $(DOCKERHUB_USER)/$$img:dev; \
		docker push $(DOCKERHUB_USER)/$$img:dev; \
	done; \
	echo ""; \
	echo "Published $$TAG. :dev tags updated."

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
		docker pull $(DOCKERHUB_USER)/$$img:$$TAG_TO_USE; \
		docker tag $(DOCKERHUB_USER)/$$img:$$TAG_TO_USE $(DOCKERHUB_USER)/$$img:staging; \
		docker push $(DOCKERHUB_USER)/$$img:staging; \
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
		docker pull $(DOCKERHUB_USER)/$$img:$$TAG_TO_USE; \
		docker tag $(DOCKERHUB_USER)/$$img:$$TAG_TO_USE $(DOCKERHUB_USER)/$$img:latest; \
		docker push $(DOCKERHUB_USER)/$$img:latest; \
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
	$(COMPOSE_CMD) exec -T meeting-api python -c "import asyncio; from admin_models.database import init_db; asyncio.run(init_db())"; \
	$(COMPOSE_CMD) exec -T meeting-api python -c "import asyncio; from meeting_api.database import init_db; asyncio.run(init_db())"; \
	echo "Schema sync complete."

# Restore a production dump into the local postgres.
# Extracts only the public schema, loads data, then runs schema sync.
#   make restore-db DUMP=~/secrets/production-dump.sql
restore-db: check_docker
	@if [ -z "$(DUMP)" ]; then \
		echo "Usage: make restore-db DUMP=<path-to-dump.sql>"; exit 1; \
	fi
	$(ROOT)/deploy/compose/scripts/restore-prod-dump.sh "$(DUMP)"
