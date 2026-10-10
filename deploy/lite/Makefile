LITE_MAKEFILE := $(abspath $(lastword $(MAKEFILE_LIST)))

# =============================================================================
# Vexa Lite (v0.12) — single-container build + deploy
# =============================================================================
# `make lite` (from repo root) → this Makefile's `all`: provisions the PostgreSQL + storage sidecar
# containers (storage = versitygw, an S3 server that keeps recordings as plain files in a volume),
# pulls/builds the all-in-one image, runs it with the process backend, and verifies the front doors.
# The app image is lean; postgres and storage stay external.
.PHONY: all up down init-db test env preflight build push build-and-push set-latest set-dev setup-builder help stt-smoke storage-up storage-where migrate-storage

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
# port collisions); only the front doors are published. postgres and storage join the same bridge and
# the app reaches them by container name. Override HOST_*_PORT to avoid conflicts on a busy host.
NETWORK           ?= vexa-lite-net
PG_CONTAINER      ?= vexa-lite-postgres
PG_IMAGE          ?= postgres:17-alpine
STORAGE_CONTAINER ?= vexa-lite-storage
APP_CONTAINER     ?= vexa-lite
PG_VOLUME         ?= vexa-lite-pgdata
# The app container's own state (/var/lib/vexa/state): the terminal's NEXTAUTH_SECRET and agent-api's
# VEXA_DISPATCH_SIGNING_KEY, minted on first boot when .env sets none, so re-creating the container
# keeps sign-in cookies valid and the same signing key.
STATE_VOLUME      ?= vexa-lite-state
HOST_GATEWAY_PORT   ?= 8056
HOST_TERMINAL_PORT  ?= 3001
HOST_AGENT_PORT     ?= 8100
# Storage: versitygw (Apache-2.0) with its POSIX backend, pinned by digest. Objects are plain files
# under data/<bucket>/ in STORAGE_VOLUME; S3 metadata (content type, ETag) rides in extended
# attributes, so back the volume up with an attribute-preserving copy (rsync -aX, cp -a).
# meeting-api keeps its MINIO_* variable names; they now point at this sidecar.
STORAGE_IMAGE     ?= versity/versitygw:v1.8.0@sha256:30292fc2eeacc67a36993b01f7a7a5e3361a19cced0e80c1d71cfa2a4b0a2499
STORAGE_VOLUME    ?= vexa-lite-storagedata
# The storage root credentials have no default: they live in .env (MINIO_ACCESS_KEY, MINIO_SECRET_KEY),
# minted there by storage-credentials.sh when unset or published. Set both here to use your own; a
# published value is refused either way.
MINIO_ACCESS_KEY ?=
MINIO_SECRET_KEY ?=
MINIO_BUCKET     ?= vexa
# Opt-in copy from an older Lite: only migrate-storage uses the legacy container and volume. Its MinIO
# was started with the pair every older Lite Makefile defaulted to; pass the pair you gave it instead.
LEGACY_MINIO_CONTAINER  ?= vexa-lite-minio
LEGACY_MINIO_VOLUME     ?= vexa-lite-miniodata
LEGACY_MINIO_IMAGE      ?=
LEGACY_MINIO_ACCESS_KEY ?= vexa-access-key
LEGACY_MINIO_SECRET_KEY ?= vexa-secret-key
LEGACY_MINIO_BUCKET     ?= $(MINIO_BUCKET)
# The values the storage root refuses, the list compose's storage and Lite's entrypoint refuse.
STORAGE_PUBLISHED := ""|vexa-access-key|vexa-secret-key|minioadmin|changeme|change-me|CHANGE-ME|default|secret|password

# --- Local CPU STT (LOCAL_STT=1) ---------------------------------------------------------------
# Transcription in Vexa is always an external OpenAI-compatible /v1/audio/transcriptions service.
# For a self-host (or a witness box) with NO hosted token and NO GPU, `make up LOCAL_STT=1` runs a
# bundled faster-whisper CPU server on the tiny model and auto-wires TRANSCRIPTION_SERVICE_URL to
# it — transcripts work out of the box, slower but real. The client sends model=whisper-1 (the
# OpenAI id); faster-whisper-server accepts it and serves WHISPER_MODEL. Override for a bigger
# model (WHISPER_MODEL=Systran/faster-whisper-small.en) or a GPU image.
LOCAL_STT        ?=
WHISPER_CONTAINER ?= vexa-lite-whisper
WHISPER_IMAGE    ?= fedirz/faster-whisper-server:latest-cpu
WHISPER_MODEL    ?= Systran/faster-whisper-tiny.en
HOST_STT_PORT    ?= 8083

define set_tag
	@echo "Setting '$(1)' → $(2)..."
	docker buildx imagetools create -t $(DOCKERHUB_USER)/$(IMAGE_NAME):$(1) $(DOCKERHUB_USER)/$(IMAGE_NAME):$(2)
endef

help:
	@echo "Vexa Lite (v0.12)"
	@echo "  make -C deploy/lite all     # provision + run + verify (this is `make lite` from root)"
	@echo "  make -C deploy/lite down    # stop + remove app + sidecars"
	@echo "  make -C deploy/lite migrate-storage  # copy recordings from a pre-versitygw (MinIO) Lite, verified"
	@echo "  make -C deploy/lite storage-where    # warn if .env selects an S3 endpoint"
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
			printf 'ADMIN_TOKEN=\nTRANSCRIPTION_SERVICE_URL=\nTRANSCRIPTION_SERVICE_TOKEN=\nIMAGE_TAG=v012\n' > "$(ENV_FILE)"; \
		fi; \
	fi

## preflight — Docker must be up.
preflight:
	@docker info > /dev/null 2>&1 || { echo "ERROR: Docker is not running."; exit 1; }

## storage-up — the storage sidecar: versitygw with its POSIX backend on STORAGE_VOLUME (data/ holds the
## buckets as directories, iam/ the gateway's account file), then the bucket. `versitygw admin
## create-bucket` exits 1 when the bucket already exists, so BucketAlreadyOwnedByYou (or, for a bucket
## an earlier root key created, BucketAlreadyExists) also counts as ready.
## Its S3 port (with the admin API on the same port) is reachable only on NETWORK, never published.
##
## Root credentials: MINIO_ACCESS_KEY / MINIO_SECRET_KEY from the make command line, else from .env,
## where storage-credentials.sh mints them when unset or published. A published value is refused. A
## running sidecar started with other credentials (an install from before they were minted) is
## recreated with these: versitygw takes its root key from its environment at start, the volume
## keeps every object, and the new root reads buckets the old one created. Values are never printed.
storage-up: preflight
	@set -e; \
	envv() { grep -E "^$$1=" $(ENV_FILE) 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$$//; s/[[:space:]]+$$//'; }; \
	AK="$(MINIO_ACCESS_KEY)"; SK="$(MINIO_SECRET_KEY)"; \
	if [ -z "$$AK" ] && [ -z "$$SK" ]; then sh $(ROOT)/deploy/lite/storage-credentials.sh $(ENV_FILE); AK=$$(envv MINIO_ACCESS_KEY); SK=$$(envv MINIO_SECRET_KEY); fi; \
	case "$$AK" in $(STORAGE_PUBLISHED)) echo "  ✗ storage: MINIO_ACCESS_KEY is unset or published in the Vexa repository"; exit 1;; esac; \
	case "$$SK" in $(STORAGE_PUBLISHED)) echo "  ✗ storage: MINIO_SECRET_KEY is unset or published in the Vexa repository"; exit 1;; esac; \
	docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK) >/dev/null; \
	echo "Starting storage ($(STORAGE_CONTAINER), versitygw)..."; \
	WANT=$$(printf 'ROOT_ACCESS_KEY=%s\nROOT_SECRET_KEY=%s' "$$AK" "$$SK"); \
	HAVE=$$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $(STORAGE_CONTAINER) 2>/dev/null | grep -E '^ROOT_(ACCESS|SECRET)_KEY=' | sort); \
	if docker ps -q -f name=^$(STORAGE_CONTAINER)$$ | grep -q . && [ "$$WANT" != "$$HAVE" ]; then \
		echo "  Storage root credentials changed: recreating $(STORAGE_CONTAINER) (volume $(STORAGE_VOLUME) kept)"; \
		docker rm -f $(STORAGE_CONTAINER) >/dev/null; \
	fi; \
	if ! docker ps -q -f name=^$(STORAGE_CONTAINER)$$ | grep -q .; then \
		docker rm -f $(STORAGE_CONTAINER) 2>/dev/null || true; \
		docker run -d --name $(STORAGE_CONTAINER) --network $(NETWORK) --restart unless-stopped \
			-e ROOT_ACCESS_KEY="$$AK" -e ROOT_SECRET_KEY="$$SK" \
			-v $(STORAGE_VOLUME):/srv/vexa-storage --entrypoint /bin/sh $(STORAGE_IMAGE) -c \
			'mkdir -p /srv/vexa-storage/data /srv/vexa-storage/iam && exec versitygw --port :9000 --health /health --iam-dir /srv/vexa-storage/iam --quiet posix /srv/vexa-storage/data' > /dev/null; \
	fi; \
	for i in $$(seq 1 30); do docker exec $(STORAGE_CONTAINER) wget -q -O /dev/null http://127.0.0.1:9000/health 2>/dev/null && break; sleep 1; done; \
	out=$$(docker run --rm --network $(NETWORK) -e ADMIN_ACCESS_KEY="$$AK" -e ADMIN_SECRET_KEY="$$SK" \
		$(STORAGE_IMAGE) admin --endpoint-url http://$(STORAGE_CONTAINER):9000 create-bucket --bucket $(MINIO_BUCKET) --owner "$$AK" 2>&1) \
		|| echo "$$out" | grep -q -E 'BucketAlreadyOwnedByYou|BucketAlreadyExists' \
		|| { echo "  ✗ storage: could not create bucket $(MINIO_BUCKET): $$out"; exit 1; }; \
	echo "  ✓ Storage ready (bucket $(MINIO_BUCKET), volume $(STORAGE_VOLUME))"

## migrate-storage — copy every object from the MinIO sidecar of an older Lite into the new storage and
## verify each by SHA-256 (deploy/storage/migrate-from-minio.sh + s3_copy.py, run with the Lite image's
## Python). Resumable; never deletes. Reads through the old container if it still runs, else a temporary
## MinIO on the old volume from a locally present image (LEGACY_MINIO_IMAGE); stops clearly without one.
migrate-storage: preflight storage-up
	@set -e; \
	envv() { grep -E "^$$1=" $(ENV_FILE) 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$$//; s/[[:space:]]+$$//'; }; \
	IMAGE_TAG=$$(envv IMAGE_TAG); IMAGE_TAG=$${IMAGE_TAG:-v012}; \
	IMG=$$(docker image inspect $(LOCAL_TAG) >/dev/null 2>&1 && echo $(LOCAL_TAG) || echo $(DOCKERHUB_USER)/$(IMAGE_NAME):$$IMAGE_TAG); \
	AK="$(MINIO_ACCESS_KEY)"; AK=$${AK:-$$(envv MINIO_ACCESS_KEY)}; SK="$(MINIO_SECRET_KEY)"; SK=$${SK:-$$(envv MINIO_SECRET_KEY)}; \
	NETWORK=$(NETWORK) LEGACY_VOLUME=$(LEGACY_MINIO_VOLUME) LEGACY_CONTAINER=$(LEGACY_MINIO_CONTAINER) \
	LEGACY_IMAGE="$(LEGACY_MINIO_IMAGE)" LEGACY_ACCESS_KEY="$(LEGACY_MINIO_ACCESS_KEY)" \
	LEGACY_SECRET_KEY="$(LEGACY_MINIO_SECRET_KEY)" LEGACY_BUCKET=$(LEGACY_MINIO_BUCKET) \
	TARGET_ENDPOINT=http://$(STORAGE_CONTAINER):9000 TARGET_ACCESS_KEY="$$AK" \
	TARGET_SECRET_KEY="$$SK" TARGET_BUCKET=$(MINIO_BUCKET) STORAGE_VOLUME=$(STORAGE_VOLUME) \
	RUN_IMAGE="$$IMG" RUN_PYTHON=/opt/venvs/meeting/bin/python TOOLS_DIR=$(ROOT)/deploy/storage \
	NAME_PREFIX=$(APP_CONTAINER) RETRY_HINT="make -C deploy/lite migrate-storage" COPY_ARGS="$(COPY_ARGS)" \
	$(ROOT)/deploy/storage/migrate-from-minio.sh

## up — a bridge network, postgres + storage sidecars (by name), then the all-in-one container with
## only the front doors published. Internal services live on the container's own loopback.
##
## The recipe is ONE `\`-continued shell command: a bare `#` line inside it (no trailing `\`) ends
## the continuation, so make runs the rest in a SEPARATE shell where IMG/ADMIN_TK/… are already
## lost — which once shipped `docker run … $IMG` with an EMPTY image (vexa-lite never created,
## every front door down). Keep all commentary OUT of the recipe body; annotate here instead.
##
## Claude-credential automount (C2/#502): if the host has ~/.claude/.credentials.json, mount THAT FILE
## read-only into a root-only directory (/var/lib/vexa/host-claude, 0700 in the image) and point
## HOST_CLAUDE_CREDENTIALS at it — in LITE the runtime copies it into each worker's private HOME.
## (Compose is the opposite: there the var is a docker-HOST path the compose file mounts. Don't copy
## the semantics across.) Only the file: the rest of ~/.claude (transcripts, history, settings) is
## the operator's and never enters the container. A single-file bind pins the inode it was created
## with, and the CLI refreshes a token by rename(2)-ing a new file over it, so after the host CLI
## refreshes, re-run `make up` (or set CLAUDE_CODE_OAUTH_TOKEN / an API key instead).
##
## `envv` strips an INLINE COMMENT and trailing space from a .env value. `.env.example` once shipped
## `ADMIN_TOKEN=dev-admin-token   # admin-api auth…`; a bare `cut -d= -f2-` kept the comment, and the
## unquoted `-e ADMIN_API_TOKEN=$ADMIN_TK` then word-split it so a lone `#` landed in the IMAGE slot
## — `docker: invalid reference format`, the v0.12.5 witness's finding #3. Values interpolated into
## `docker run` are QUOTED for the same reason. `$$CLAUDE_MOUNT` is the deliberate exception: it
## carries multiple argv words and MUST stay unquoted — do not "fix" it.
##
## The database password lives in .env (DB_PASSWORD), never as a default: an unset or published value
## is replaced by a minted one written back to .env, and every `up` sets the .env value on the postgres
## sidecar over its local socket (ALTER USER), so a volume created with the old published password
## moves to it without a separate step. The value is never printed.
##
## Note this recipe does NOT `-e` the spawn-default flags (TRANSCRIBE_ENABLED/RECORDING_ENABLED);
## they ride `--env-file`, where an empty value must keep the default. That is enforced in code
## (bot_spawn/env_flags.py), not here — compose's `${VAR:-true}` has no `--env-file` equivalent.
up: preflight storage-up
	@set -e; \
	docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK) >/dev/null; \
	envv() { grep -E "^$$1=" $(ENV_FILE) 2>/dev/null | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$$//; s/[[:space:]]+$$//'; }; \
	ADMIN_TK=$$(envv ADMIN_TOKEN); \
	TX_URL=$$(envv TRANSCRIPTION_SERVICE_URL); \
	TX_TOKEN=$$(envv TRANSCRIPTION_SERVICE_TOKEN); \
	IMAGE_TAG=$$(envv IMAGE_TAG); IMAGE_TAG=$${IMAGE_TAG:-v012}; \
	IMG=$$(docker image inspect $(LOCAL_TAG) >/dev/null 2>&1 && echo $(LOCAL_TAG) || echo $(DOCKERHUB_USER)/$(IMAGE_NAME):$$IMAGE_TAG); \
	MINIO_AK="$(MINIO_ACCESS_KEY)"; MINIO_AK=$${MINIO_AK:-$$(envv MINIO_ACCESS_KEY)}; \
	MINIO_SK="$(MINIO_SECRET_KEY)"; MINIO_SK=$${MINIO_SK:-$$(envv MINIO_SECRET_KEY)}; \
	DB_PW=$$(envv DB_PASSWORD); \
	case "$$DB_PW" in \
		""|postgres|password|vexa-internal-secret|lite-internal-secret|changeme|change-me|CHANGE-ME|default|secret) \
			DB_PW=$$(openssl rand -hex 32); \
			if grep -qE '^DB_PASSWORD=' "$(ENV_FILE)"; then sed -i.bak "s|^DB_PASSWORD=.*|DB_PASSWORD=$$DB_PW|" "$(ENV_FILE)" && rm -f "$(ENV_FILE).bak"; \
			else printf '\nDB_PASSWORD=%s\n' "$$DB_PW" >> "$(ENV_FILE)"; fi; \
			echo "  Minted a database password into $(ENV_FILE) (DB_PASSWORD)";; \
	esac; \
	CLAUDE_CREDS="$$HOME/.claude/.credentials.json"; \
	if [ -f "$$CLAUDE_CREDS" ]; then \
		echo "  Detected Claude subscription credentials — mounting the credential file into the container"; \
		CLAUDE_MOUNT="-v $$CLAUDE_CREDS:/var/lib/vexa/host-claude/.credentials.json:ro -e HOST_CLAUDE_CREDENTIALS=/var/lib/vexa/host-claude/.credentials.json"; \
	else \
		CLAUDE_MOUNT=""; \
	fi; \
	echo "Starting PostgreSQL ($(PG_CONTAINER))..."; \
	if ! docker ps -q -f name=$(PG_CONTAINER) | grep -q .; then \
		docker rm -f $(PG_CONTAINER) 2>/dev/null || true; \
		docker run -d --name $(PG_CONTAINER) --network $(NETWORK) --restart unless-stopped \
			-e POSTGRES_DB=vexa -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$$DB_PW" \
			-e TZ=UTC -e PGTZ=UTC \
			-v $(PG_VOLUME):/var/lib/postgresql/data \
			$(PG_IMAGE) -c idle_in_transaction_session_timeout=60000 > /dev/null; \
		for i in $$(seq 1 24); do docker exec $(PG_CONTAINER) pg_isready -U postgres -d vexa -q 2>/dev/null && break; sleep 2; done; \
	fi; \
	printf "ALTER USER postgres PASSWORD '%s';\n" "$$(printf '%s' "$$DB_PW" | sed "s/'/''/g")" \
		| docker exec -i $(PG_CONTAINER) psql -U postgres -d vexa -v ON_ERROR_STOP=1 -q >/dev/null; \
	echo "  ✓ PostgreSQL ready"; \
	if [ -n "$(LOCAL_STT)" ]; then \
		echo "Starting local CPU STT ($(WHISPER_CONTAINER), $(WHISPER_MODEL))..."; \
		if ! docker ps -q -f name=$(WHISPER_CONTAINER) | grep -q .; then \
			docker rm -f $(WHISPER_CONTAINER) 2>/dev/null || true; \
			docker run -d --name $(WHISPER_CONTAINER) --network $(NETWORK) --restart unless-stopped \
				-e WHISPER__MODEL=$(WHISPER_MODEL) -e WHISPER__INFERENCE_DEVICE=cpu -e WHISPER__TTL=-1 \
				-p $(HOST_STT_PORT):8000 \
				$(WHISPER_IMAGE) > /dev/null; \
		fi; \
		echo "  Waiting for the STT model to load (up to 3 min)..."; \
		for i in $$(seq 1 36); do curl -sf -o /dev/null http://localhost:$(HOST_STT_PORT)/health 2>/dev/null && break; sleep 5; done; \
		TX_URL=http://$(WHISPER_CONTAINER):8000; TX_TOKEN=; \
		echo "  ✓ local STT ready — routing transcription to $$TX_URL (model $(WHISPER_MODEL))"; \
	fi; \
	echo "Starting Vexa Lite ($$IMG)..."; \
	docker rm -f $(APP_CONTAINER) 2>/dev/null || true; \
	docker run -d --name $(APP_CONTAINER) --shm-size=2g --network $(NETWORK) --restart unless-stopped \
		--env-file $(ENV_FILE) \
		-v $(STATE_VOLUME):/var/lib/vexa/state \
		-p $(HOST_GATEWAY_PORT):8056 -p $(HOST_TERMINAL_PORT):3001 -p $(HOST_AGENT_PORT):8100 \
		-e DB_HOST=$(PG_CONTAINER) -e DB_PORT=5432 -e DB_NAME=vexa -e DB_USER=postgres -e DB_PASSWORD="$$DB_PW" \
		-e ADMIN_API_TOKEN="$$ADMIN_TK" -e ADMIN_TOKEN="$$ADMIN_TK" \
		-e TRANSCRIPTION_SERVICE_URL="$$TX_URL" -e TRANSCRIPTION_SERVICE_TOKEN="$$TX_TOKEN" \
		-e MINIO_ENDPOINT=$(STORAGE_CONTAINER):9000 -e MINIO_ACCESS_KEY="$$MINIO_AK" \
		-e MINIO_SECRET_KEY="$$MINIO_SK" -e MINIO_BUCKET=$(MINIO_BUCKET) -e MINIO_SECURE=false \
		-e VEXA_PUBLIC_API_URL=http://localhost:$(HOST_GATEWAY_PORT) \
		-e TERMINAL_PUBLIC_URL=http://localhost:$(HOST_TERMINAL_PORT) \
		$$CLAUDE_MOUNT \
		"$$IMG" > /dev/null; \
	echo "  Waiting for the gateway (up to 3 min)..."; \
	for i in $$(seq 1 36); do curl -sf -o /dev/null http://localhost:$(HOST_GATEWAY_PORT)/health 2>/dev/null && break; sleep 5; done; \
	echo "  ✓ Vexa Lite running"
	@$(MAKE) --no-print-directory -f $(LITE_MAKEFILE) storage-where

## storage-where — report an explicit recording store without requiring Docker.
storage-where:
	@endpoint=$$(awk '/^[[:space:]]*S3_ENDPOINT=/ { sub(/^[^=]*=/, ""); sub(/\r$$/, ""); value=$$0 } END { print value }' "$(ENV_FILE)" 2>/dev/null); \
	if [ -n "$$endpoint" ]; then \
		host=$$(printf '%s' "$$endpoint" | sed -E 's|^[^:/]+://||; s|[:/].*$$||' | tr '[:upper:]' '[:lower:]'); \
		bundled=$$(printf '%s' '$(STORAGE_CONTAINER)' | tr '[:upper:]' '[:lower:]'); \
		if [ "$$host" != "$$bundled" ]; then \
			printf '%s\n' "WARNING: recordings go to $$endpoint (S3_ENDPOINT in $(ENV_FILE)), not the bundled storage ($(STORAGE_CONTAINER)). Expected if that is your own S3; if this install was upgraded from MinIO, see https://docs.vexa.ai/upgrade-from-minio"; \
		fi; \
	fi; true

## init-db — schema converges on each service's startup (admin/meeting ensure_schema); no-op hook.
init-db:
	@echo "  ✓ Schema converges in-process on service startup"

## test — probe the published front doors. Each door gets up to TEST_WAIT_SECONDS to answer: on first
## boot provision-key.sh restarts the dashboard and terminal once the self-host key is minted. Then
## read every process's environment inside the container (tests/program_environments.py): the
## runtime caller credential is held by the runtime, agent-api and meeting-api and nothing else.
## Then start a worker for a numeric and a named subject, a bot, and refused dispatches through the
## runtime (tests/child_identities.py): no child is root, each runs as its own uid, and none can read
## another process's environment, a secret on any command line, or root's state.
TEST_WAIT_SECONDS ?= 120
test:
	@FAIL=0; for ep in "$(HOST_GATEWAY_PORT):gateway" "$(HOST_AGENT_PORT):agent-api" "$(HOST_TERMINAL_PORT):terminal"; do \
		PORT=$${ep%%:*}; NAME=$${ep##*:}; OK=0; WAITED=0; \
		while :; do \
			if curl -sf -o /dev/null http://localhost:$$PORT/ 2>/dev/null || curl -sf -o /dev/null http://localhost:$$PORT/health 2>/dev/null; then OK=1; break; fi; \
			[ $$WAITED -ge $(TEST_WAIT_SECONDS) ] && break; sleep 5; WAITED=$$((WAITED + 5)); \
		done; \
		if [ $$OK = 1 ]; then echo "  ✓ $$NAME (:$$PORT)"; else echo "  ✗ $$NAME not responding after $(TEST_WAIT_SECONDS)s (:$$PORT)"; FAIL=1; fi; \
	done; \
	docker exec -i $(APP_CONTAINER) python3 - < "$(ROOT)/deploy/lite/tests/program_environments.py" || FAIL=1; \
	docker exec -i $(APP_CONTAINER) python3 - < "$(ROOT)/deploy/lite/tests/child_identities.py" || FAIL=1; \
	exit $$FAIL

## down — stop + remove app + sidecars + network (volumes persist; `docker volume rm vexa-lite-pgdata vexa-lite-storagedata` to wipe).
## A MinIO sidecar left by an older Lite (LEGACY_MINIO_CONTAINER) and its volume are not touched.
down:
	@echo "Stopping Vexa Lite..."
	@docker rm -f $(APP_CONTAINER) $(PG_CONTAINER) $(STORAGE_CONTAINER) $(WHISPER_CONTAINER) 2>/dev/null || true
	@docker network rm $(NETWORK) 2>/dev/null || true
	@echo "  ✓ Stopped (data volumes kept)"

## stt-smoke — prove the local STT actually transcribes: synthesize speech, POST it as the client
## does (model=whisper-1, verbose_json), and assert non-empty text. The machine check that the
## witness box will transcribe BEFORE a human is asked to speak (validation ladder: machine first).
## Requires LOCAL_STT to be up (HOST_STT_PORT published). Uses containers for espeak/ffmpeg — no
## host tools needed.
stt-smoke:
	@set -e; \
	echo "Synthesizing test speech + transcribing via local STT (:$(HOST_STT_PORT), model=whisper-1)..."; \
	docker run --rm -v /tmp:/out --entrypoint sh $(WHISPER_IMAGE) -c 'exit 0' 2>/dev/null || true; \
	TMP=$$(mktemp -d); \
	docker run --rm -v $$TMP:/out debian:stable-slim sh -c \
		'apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq espeak-ng ffmpeg >/dev/null 2>&1 && \
		 espeak-ng "the quick brown fox jumps over the lazy dog" -w /tmp/s.wav 2>/dev/null && \
		 ffmpeg -y -i /tmp/s.wav -ar 16000 -ac 1 /out/speech.wav >/dev/null 2>&1'; \
	OUT=$$(curl -s --max-time 90 http://localhost:$(HOST_STT_PORT)/v1/audio/transcriptions \
		-F file=@$$TMP/speech.wav -F model=whisper-1 -F response_format=verbose_json); \
	rm -rf $$TMP; \
	echo "  STT response: $$OUT" | head -c 300; echo; \
	echo "$$OUT" | grep -qiE 'quick brown fox|lazy dog' \
		&& echo "  ✓ local STT transcribes (model=whisper-1 → words)" \
		|| { echo "  ✗ local STT returned no expected words — check $(WHISPER_CONTAINER) logs"; exit 1; }

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
