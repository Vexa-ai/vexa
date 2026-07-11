# =============================================================================
# Vexa Lite (v0.12) — single-container build + deploy
# =============================================================================
# `make lite` (from repo root) → this Makefile's `all`: provisions PostgreSQL + MinIO sidecar
# containers, pulls/builds the all-in-one image, runs it on the host network with the process
# backend, and verifies the front doors. The app image is lean; postgres/minio stay external.
.PHONY: all up down init-db test env preflight build push build-and-push set-latest set-dev setup-builder help

# --- Paths ---
ROOT       := $(shell git rev-parse --show-toplevel)
ENV_FILE   := $(ROOT)/.env
DOCKERFILE := $(ROOT)/deploy/lite/Dockerfile.lite
CONTEXT    := $(ROOT)

# --- DockerHub / image ---
DOCKERHUB_USER ?= vexaai
IMAGE_NAME     ?= vexa-lite
TAG            ?= dev
LOCAL_TAG      := $(IMAGE_NAME):$(TAG)
PLATFORMS      ?= linux/amd64,linux/arm64
BUILDER_NAME   ?= vexa-multiarch
UNIQUE_TAG     := $(shell date +%y%m%d-%H%M)
UNIQUE_DOCKERHUB_TAG := $(DOCKERHUB_USER)/$(IMAGE_NAME):$(UNIQUE_TAG)
LAST_TAG_FILE  := $(ROOT)/deploy/lite/.last-tag

# --- Network + sidecars + storage defaults ---
# A user-defined bridge so the app's INTERNAL services live on the container's own loopback (no host
# port collisions); only the front doors are published. postgres/minio join the same bridge and the
# app reaches them by container name. Override HOST_*_PORT to avoid conflicts on a busy host.
NETWORK         ?= vexa-lite-net
PG_CONTAINER    ?= vexa-lite-postgres
MINIO_CONTAINER ?= vexa-lite-minio
APP_CONTAINER   ?= vexa-lite
HOST_GATEWAY_PORT   ?= 8056
HOST_TERMINAL_PORT  ?= 3001
HOST_AGENT_PORT     ?= 8100
MINIO_ACCESS_KEY ?= vexa-access-key
MINIO_SECRET_KEY ?= vexa-secret-key
MINIO_BUCKET     ?= vexa

define set_tag
	@echo "Setting '$(1)' → $(2)..."
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):$(1) $(DOCKERHUB_USER)/$(IMAGE_NAME):$(2)
endef

help:
	@echo "Vexa Lite (v0.12)"
	@echo "  make -C deploy/lite all     # provision + run + verify (this is `make lite` from root)"
	@echo "  make -C deploy/lite down    # stop + remove app + sidecars"
	@echo "  make -C deploy/lite build   # build the image locally (tagged :dev)"
	@echo "  make -C deploy/lite push    # multi-arch build+push, move the 'dev' tag"
	@echo "  make -C deploy/lite test    # probe the front doors"

# ── Deploy lifecycle ──────────────────────────────────────────────────────────────────────────────
all: env preflight up init-db test
	@HOST=$$(hostname -I 2>/dev/null | awk '{print $$1}'); HOST=$${HOST:-localhost}; \
	echo ""; echo "Vexa Lite is running."; \
	echo "  Terminal:  http://$$HOST:$(HOST_TERMINAL_PORT)"; \
	echo "  API docs:  http://$$HOST:$(HOST_GATEWAY_PORT)/docs"; \
	echo "  Agent API: http://$$HOST:$(HOST_AGENT_PORT)"

## env — ensure a repo-root .env exists (seed from compose/.env if present, else a minimal template).
env:
	@if [ ! -f "$(ENV_FILE)" ]; then \
		if [ -f "$(ROOT)/deploy/compose/.env" ]; then \
			echo "Seeding $(ENV_FILE) from deploy/compose/.env"; cp "$(ROOT)/deploy/compose/.env" "$(ENV_FILE)"; \
		else \
			echo "Creating minimal $(ENV_FILE) (edit TRANSCRIPTION_SERVICE_* for transcripts)"; \
			printf 'ADMIN_TOKEN=changeme\nTRANSCRIPTION_SERVICE_URL=\nTRANSCRIPTION_SERVICE_TOKEN=\nIMAGE_TAG=latest\n' > "$(ENV_FILE)"; \
		fi; \
	fi

## preflight — Docker must be up.
preflight:
	@docker info > /dev/null 2>&1 || { echo "ERROR: Docker is not running."; exit 1; }

## up — a bridge network, postgres + minio sidecars (by name), then the all-in-one container with
## only the front doors published. Internal services live on the container's own loopback.
up: preflight
	@set -e; \
	docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK) >/dev/null; \
	ADMIN_TK=$$(grep -E '^ADMIN_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2-); ADMIN_TK=$${ADMIN_TK:-changeme}; \
	TX_URL=$$(grep -E '^TRANSCRIPTION_SERVICE_URL=' $(ENV_FILE) 2>/dev/null | cut -d= -f2-); \
	TX_TOKEN=$$(grep -E '^TRANSCRIPTION_SERVICE_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2-); \
	IMAGE_TAG=$$(grep -E '^IMAGE_TAG=' $(ENV_FILE) 2>/dev/null | cut -d= -f2-); IMAGE_TAG=$${IMAGE_TAG:-latest}; \
	IMG=$$(docker image inspect $(LOCAL_TAG) >/dev/null 2>&1 && echo $(LOCAL_TAG) || echo $(DOCKERHUB_USER)/$(IMAGE_NAME):$$IMAGE_TAG); \
	# Autodetect host Claude credentials and wire the mount automatically (C2/#502)
	CLAUDE_CREDS="$$HOME/.claude/.credentials.json"; \
	if [ -f "$$CLAUDE_CREDS" ]; then \
		echo "  Detected Claude subscription credentials — mounting into container"; \
		CLAUDE_MOUNT="-v $$CLAUDE_CREDS:/var/lib/vexa/host-claude-credentials:ro -e HOST_CLAUDE_CREDENTIALS=$$CLAUDE_CREDS"; \
	else \
		CLAUDE_MOUNT=""; \
	fi; \
	echo "Starting PostgreSQL ($(PG_CONTAINER))..."; \
	if ! docker ps -q -f name=$(PG_CONTAINER) | grep -q .; then \
		docker rm -f $(PG_CONTAINER) 2>/dev/null || true; \
		docker run -d --name $(PG_CONTAINER) --network $(NETWORK) --restart unless-stopped \
			-e POSTGRES_DB=vexa -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
			-e TZ=UTC -e PGTZ=UTC \
			-v vexa-lite-pgdata:/var/lib/postgresql/data \
			postgres:17-alpine -c idle_in_transaction_session_timeout=60000 > /dev/null; \
		for i in $$(seq 1 24); do docker exec $(PG_CONTAINER) pg_isready -U postgres -d vexa -q 2>/dev/null && break; sleep 2; done; \
	fi; echo "  ✓ PostgreSQL ready"; \
	echo "Starting MinIO ($(MINIO_CONTAINER))..."; \
	if ! docker ps -q -f name=$(MINIO_CONTAINER) | grep -q .; then \
		docker rm -f $(MINIO_CONTAINER) 2>/dev/null || true; \
		docker run -d --name $(MINIO_CONTAINER) --network $(NETWORK) --restart unless-stopped \
			-e MINIO_ROOT_USER=$(MINIO_ACCESS_KEY) -e MINIO_ROOT_PASSWORD=$(MINIO_SECRET_KEY) \
			-v vexa-lite-miniodata:/data \
			minio/minio:latest server /data --console-address ":9001" > /dev/null; \
		sleep 4; \
		docker run --rm --network $(NETWORK) --entrypoint sh minio/mc:latest -c \
			"mc alias set vexa http://$(MINIO_CONTAINER):9000 $(MINIO_ACCESS_KEY) $(MINIO_SECRET_KEY) && mc mb --ignore-existing vexa/$(MINIO_BUCKET)" >/dev/null 2>&1 || true; \
	fi; echo "  ✓ MinIO ready"; \
	echo "Starting Vexa Lite ($$IMG)..."; \
	docker rm -f $(APP_CONTAINER) 2>/dev/null || true; \
	docker run -d --name $(APP_CONTAINER) --shm-size=2g --network $(NETWORK) --restart unless-stopped \
		--env-file $(ENV_FILE) \
		-p $(HOST_GATEWAY_PORT):8056 -p $(HOST_TERMINAL_PORT):3001 -p $(HOST_AGENT_PORT):8100 \
		-e DB_HOST=$(PG_CONTAINER) -e DB_PORT=5432 -e DB_NAME=vexa -e DB_USER=postgres -e DB_PASSWORD=postgres \
		-e ADMIN_API_TOKEN=$$ADMIN_TK -e ADMIN_TOKEN=$$ADMIN_TK \
		-e TRANSCRIPTION_SERVICE_URL=$$TX_URL -e TRANSCRIPTION_SERVICE_TOKEN=$$TX_TOKEN \
		-e MINIO_ENDPOINT=$(MINIO_CONTAINER):9000 -e MINIO_ACCESS_KEY=$(MINIO_ACCESS_KEY) \
		-e MINIO_SECRET_KEY=$(MINIO_SECRET_KEY) -e MINIO_BUCKET=$(MINIO_BUCKET) -e MINIO_SECURE=false \
		-e VEXA_PUBLIC_API_URL=http://localhost:$(HOST_GATEWAY_PORT) \
		-e TERMINAL_PUBLIC_URL=http://localhost:$(HOST_TERMINAL_PORT) \
		$$CLAUDE_MOUNT \
		$$IMG > /dev/null; \
	echo "  Waiting for the gateway (up to 3 min)..."; \
	for i in $$(seq 1 36); do curl -sf -o /dev/null http://localhost:$(HOST_GATEWAY_PORT)/health 2>/dev/null && break; sleep 5; done; \
	echo "  ✓ Vexa Lite running"

## init-db — schema converges on each service's startup (admin/meeting ensure_schema); no-op hook.
init-db:
	@echo "  ✓ Schema converges in-process on service startup"

## test — probe the published front doors.
test:
	@FAIL=0; for ep in "$(HOST_GATEWAY_PORT):gateway" "$(HOST_AGENT_PORT):agent-api" "$(HOST_TERMINAL_PORT):terminal"; do \
		PORT=$${ep%%:*}; NAME=$${ep##*:}; \
		if curl -sf -o /dev/null http://localhost:$$PORT/ 2>/dev/null || curl -sf -o /dev/null http://localhost:$$PORT/health 2>/dev/null; then \
			echo "  ✓ $$NAME (:$$PORT)"; else echo "  ✗ $$NAME not responding (:$$PORT)"; FAIL=1; fi; \
	done; exit $$FAIL

## down — stop + remove app + sidecars + network (volumes persist; `docker volume rm vexa-lite-pgdata vexa-lite-miniodata` to wipe).
down:
	@echo "Stopping Vexa Lite..."
	@docker rm -f $(APP_CONTAINER) $(PG_CONTAINER) $(MINIO_CONTAINER) 2>/dev/null || true
	@docker network rm $(NETWORK) 2>/dev/null || true
	@echo "  ✓ Stopped (data volumes kept)"

# ── Image build / publish ──────────────────────────────────────────────────────────────────────────
build: preflight
	docker build -f $(DOCKERFILE) -t $(LOCAL_TAG) $(CONTEXT)
	@echo "Built $(LOCAL_TAG)"

setup-builder:
	@docker buildx inspect $(BUILDER_NAME) >/dev/null 2>&1 || \
		docker buildx create --name $(BUILDER_NAME) --driver docker-container --bootstrap

push: setup-builder
	docker buildx build --builder $(BUILDER_NAME) --platform $(PLATFORMS) \
		-f $(DOCKERFILE) -t $(UNIQUE_DOCKERHUB_TAG) --push $(CONTEXT)
	@echo "$(UNIQUE_TAG)" > $(LAST_TAG_FILE)
	$(call set_tag,dev,$(UNIQUE_TAG))

build-and-push: push

set-latest:
	@T=$${TAG:-$$(cat $(LAST_TAG_FILE) 2>/dev/null)}; [ -n "$$T" ] || { echo "No TAG / .last-tag"; exit 1; }; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):latest $(DOCKERHUB_USER)/$(IMAGE_NAME):$$T

set-dev:
	@T=$${TAG:-$$(cat $(LAST_TAG_FILE) 2>/dev/null)}; [ -n "$$T" ] || { echo "No TAG / .last-tag"; exit 1; }; \
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):dev $(DOCKERHUB_USER)/$(IMAGE_NAME):$$T
