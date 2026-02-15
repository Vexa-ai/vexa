.PHONY: all setup submodules env force-env setup-transcription-service-env build-bot-image build build-transcription-service up up-transcription-service down down-transcription-service ps logs test test-api test-setup migrate makemigrations init-db stamp-db migrate-or-init migration-status

# Default target: Sets up everything and starts the services
all: setup-env build up migrate-or-init test

# Target to set up only the environment without Docker
setup-env: env submodules

# Target to perform all initial setup steps
setup: setup-env build-bot-image

# Initialize and update Git submodules
submodules:
	@git submodule update --init --recursive

BOT_IMAGE_NAME ?= vexa-bot:dev

# Check if Docker daemon is running
check_docker:
	@if ! docker info > /dev/null 2>&1; then \
		echo "ERROR: Docker is not running. Please start Docker Desktop or Docker daemon first."; \
		exit 1; \
	fi

-include .env

# Helper: Get COMPOSE_FILES based on REMOTE_DB
# Usage: $(call get_compose_files)
get_compose_files = $(shell REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	if [ "$$REMOTE_DB" != "true" ]; then \
		echo "-f docker-compose.yml -f docker-compose.local-db.yml"; \
	else \
		echo "-f docker-compose.yml"; \
	fi)

# Ensure transcription-service/.env exists with API_TOKEN
setup-transcription-service-env:
	@if [ "$(TRANSCRIPTION)" = "remote" ]; then \
		exit 0; \
	fi; \
	if [ ! -f services/transcription-service/.env ]; then \
		if [ -f services/transcription-service/.env.example ]; then \
			cp services/transcription-service/.env.example services/transcription-service/.env; \
		else \
			echo "# API Token for securing the service" > services/transcription-service/.env; \
			echo "API_TOKEN=$$(openssl rand -hex 16)" >> services/transcription-service/.env; \
		fi; \
	fi; \
	TRANSCRIPTION_API_TOKEN=$$(grep -E '^[[:space:]]*API_TOKEN=' services/transcription-service/.env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo ""); \
	if [ -z "$$TRANSCRIPTION_API_TOKEN" ] || [ "$$TRANSCRIPTION_API_TOKEN" = "your_secure_token_here" ]; then \
		NEW_TOKEN=$$(openssl rand -hex 16); \
		if grep -q "^API_TOKEN=" services/transcription-service/.env 2>/dev/null; then \
			sed -i.bak "s/^API_TOKEN=.*/API_TOKEN=$$NEW_TOKEN/" services/transcription-service/.env; \
			rm -f services/transcription-service/.env.bak; \
		else \
			echo "API_TOKEN=$$NEW_TOKEN" >> services/transcription-service/.env; \
		fi; \
	fi; \
	MAKE_TRANSCRIPTION=$${TRANSCRIPTION:-remote}; \
	if [ "$$MAKE_TRANSCRIPTION" = "gpu" ]; then \
		python3 scripts/update_transcription_service_env.py --file services/transcription-service/.env --device cuda --compute-type float16 >/dev/null 2>&1; \
	elif [ "$$MAKE_TRANSCRIPTION" = "cpu" ]; then \
		python3 scripts/update_transcription_service_env.py --file services/transcription-service/.env --device cpu --compute-type int8 >/dev/null 2>&1; \
	fi

# Helper function to create env file for a target
# Usage: $(call create_env_file,cpu|gpu|remote,force)
define create_env_file
	TRANSCRIPTION_TYPE=$(1); FORCE=$(2); \
	if [ "$$FORCE" != "force" ] && [ -f .env ]; then \
		echo ".env exists. Use 'make force-env TRANSCRIPTION=$$TRANSCRIPTION_TYPE' to overwrite."; \
		exit 0; \
	fi; \
	if [ "$$TRANSCRIPTION_TYPE" = "cpu" ]; then \
		ENV_FILE=env-example.cpu; \
		URL=http://transcription-lb-cpu:80/v1/audio/transcriptions; \
	elif [ "$$TRANSCRIPTION_TYPE" = "gpu" ]; then \
		ENV_FILE=env-example.gpu; \
		URL=http://transcription-lb:80/v1/audio/transcriptions; \
	elif [ "$$TRANSCRIPTION_TYPE" = "remote" ]; then \
		ENV_FILE=env-example.remote; \
		URL=https://transcription-service.dev.vexa.ai/v1/audio/transcriptions; \
	else \
		echo "Error: Invalid TRANSCRIPTION_TYPE=$$TRANSCRIPTION_TYPE. Must be 'cpu', 'gpu', or 'remote'"; \
		exit 1; \
	fi; \
	if [ ! -f $$ENV_FILE ]; then \
		echo "ADMIN_API_TOKEN=token" > $$ENV_FILE; \
		echo "LANGUAGE_DETECTION_SEGMENTS=10" >> $$ENV_FILE; \
		echo "VAD_FILTER_THRESHOLD=0.5" >> $$ENV_FILE; \
		if [ "$$TRANSCRIPTION_TYPE" = "remote" ]; then \
			echo "WHISPER_MODEL_SIZE=medium" >> $$ENV_FILE; \
			echo "DEVICE_TYPE=remote" >> $$ENV_FILE; \
			echo "WL_MAX_CLIENTS=10" >> $$ENV_FILE; \
		else \
			echo "DEVICE_TYPE=remote" >> $$ENV_FILE; \
		fi; \
		echo "BOT_IMAGE_NAME=vexa-bot:dev" >> $$ENV_FILE; \
		echo "# Remote Transcriber API Configuration" >> $$ENV_FILE; \
		echo "REMOTE_TRANSCRIBER_URL=$$URL" >> $$ENV_FILE; \
		TRANSCRIPTION_API_TOKEN=$$(grep -E '^[[:space:]]*API_TOKEN=' services/transcription-service/.env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo ""); \
		if [ -n "$$TRANSCRIPTION_API_TOKEN" ] && [ "$$TRANSCRIPTION_TYPE" != "remote" ]; then \
			echo "REMOTE_TRANSCRIBER_API_KEY=$$TRANSCRIPTION_API_TOKEN" >> $$ENV_FILE; \
		else \
			if [ "$$TRANSCRIPTION_TYPE" = "remote" ]; then \
				echo "REMOTE_TRANSCRIBER_API_KEY=your_api_key_here" >> $$ENV_FILE; \
				echo "REMOTE_TRANSCRIBER_MODEL=whisper-v3-turbo" >> $$ENV_FILE; \
				echo "REMOTE_TRANSCRIBER_TEMPERATURE=0" >> $$ENV_FILE; \
				echo "REMOTE_TRANSCRIBER_VAD_MODEL=silero" >> $$ENV_FILE; \
			else \
				echo "REMOTE_TRANSCRIBER_API_KEY=" >> $$ENV_FILE; \
			fi; \
		fi; \
		echo "# Exposed Host Ports" >> $$ENV_FILE; \
		echo "API_GATEWAY_HOST_PORT=8056" >> $$ENV_FILE; \
		echo "ADMIN_API_HOST_PORT=8057" >> $$ENV_FILE; \
		echo "TRANSCRIPTION_COLLECTOR_HOST_PORT=8123" >> $$ENV_FILE; \
		echo "POSTGRES_HOST_PORT=5438" >> $$ENV_FILE; \
		echo "# Remote Database Configuration" >> $$ENV_FILE; \
		echo "# Set REMOTE_DB=true to use remote PostgreSQL instead of local Docker postgres" >> $$ENV_FILE; \
		if [ "$$TRANSCRIPTION_TYPE" = "remote" ]; then \
			echo "# When REMOTE_DB=true: Uncomment and set the remote database credentials below" >> $$ENV_FILE; \
			echo "# DB_HOST=your-remote-db-host" >> $$ENV_FILE; \
			echo "# DB_PORT=5432" >> $$ENV_FILE; \
			echo "# DB_NAME=your-db-name" >> $$ENV_FILE; \
			echo "# DB_USER=your-db-user" >> $$ENV_FILE; \
			echo "# DB_PASSWORD=your-db-password" >> $$ENV_FILE; \
		fi; \
		echo "REMOTE_DB=false" >> $$ENV_FILE; \
		if [ "$$TRANSCRIPTION_TYPE" != "remote" ]; then \
			echo "# Docker-compose compatibility (not used in remote mode)" >> $$ENV_FILE; \
			echo "WHISPER_MODEL_SIZE=" >> $$ENV_FILE; \
			echo "WL_MAX_CLIENTS=" >> $$ENV_FILE; \
		fi; \
	fi; \
	cp $$ENV_FILE .env; \
	if [ "$$TRANSCRIPTION_TYPE" != "remote" ]; then \
		TRANSCRIPTION_API_TOKEN=$$(grep -E '^[[:space:]]*API_TOKEN=' services/transcription-service/.env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo ""); \
		if [ -n "$$TRANSCRIPTION_API_TOKEN" ] && [ "$$TRANSCRIPTION_TYPE" != "remote" ]; then \
			if grep -q "^REMOTE_TRANSCRIBER_API_KEY=" .env 2>/dev/null; then \
				sed -i.bak "s|^REMOTE_TRANSCRIBER_API_KEY=.*|REMOTE_TRANSCRIBER_API_KEY=$$TRANSCRIPTION_API_TOKEN|" .env; \
				rm -f .env.bak; \
			else \
				echo "REMOTE_TRANSCRIBER_API_KEY=$$TRANSCRIPTION_API_TOKEN" >> .env; \
			fi; \
		fi; \
	fi; \
	if [ "$$TRANSCRIPTION_TYPE" = "remote" ]; then \
		echo ""; \
		echo "============================================================================"; \
		echo "IMPORTANT: Manual API Key Required"; \
		echo "============================================================================"; \
		echo "You are using remote transcription service (TRANSCRIPTION=remote)."; \
		echo "Please set REMOTE_TRANSCRIBER_API_KEY in .env file with your API key from staging.vexa.ai"; \
		echo ""; \
		echo "To deploy a local transcription service instead, use:"; \
		echo "  make force-env TRANSCRIPTION=cpu   # For CPU-based local transcription"; \
		echo "  make force-env TRANSCRIPTION=gpu   # For GPU-based local transcription"; \
		echo "============================================================================"; \
		echo ""; \
	fi
endef

# Create .env file from example
env: setup-transcription-service-env
ifndef TRANSCRIPTION
	$(eval TRANSCRIPTION := remote)
endif
	@if [ "$(TRANSCRIPTION)" = "cpu" ] || [ "$(TRANSCRIPTION)" = "gpu" ] || [ "$(TRANSCRIPTION)" = "remote" ]; then \
		$(call create_env_file,$(TRANSCRIPTION),); \
	else \
		echo "Error: TRANSCRIPTION must be 'cpu', 'gpu', or 'remote'"; \
		exit 1; \
	fi

# Force create .env file from example (overwrite existing)
force-env: setup-transcription-service-env
ifndef TRANSCRIPTION
	$(eval TRANSCRIPTION := remote)
endif
	@if [ "$(TRANSCRIPTION)" = "cpu" ] || [ "$(TRANSCRIPTION)" = "gpu" ] || [ "$(TRANSCRIPTION)" = "remote" ]; then \
		$(call create_env_file,$(TRANSCRIPTION),force); \
	else \
		echo "Error: TRANSCRIPTION must be 'cpu', 'gpu', or 'remote'"; \
		exit 1; \
	fi

# Build the standalone vexa-bot image
build-bot-image: check_docker
	@if [ -f .env ]; then \
		ENV_BOT_IMAGE_NAME=$$(grep BOT_IMAGE_NAME .env | cut -d= -f2); \
		if [ -n "$$ENV_BOT_IMAGE_NAME" ]; then \
			docker build --platform linux/amd64 -t $$ENV_BOT_IMAGE_NAME -f services/vexa-bot/Dockerfile ./services/vexa-bot; \
		else \
			docker build --platform linux/amd64 -t $(BOT_IMAGE_NAME) -f services/vexa-bot/Dockerfile ./services/vexa-bot; \
		fi; \
	else \
		docker build --platform linux/amd64 -t $(BOT_IMAGE_NAME) -f services/vexa-bot/Dockerfile ./services/vexa-bot; \
	fi

# Build transcription-service based on TRANSCRIPTION
build-transcription-service: check_docker
	@if [ "$(TRANSCRIPTION)" = "remote" ]; then \
		exit 0; \
	elif [ "$(TRANSCRIPTION)" = "cpu" ]; then \
		cd services/transcription-service && docker compose -f docker-compose.cpu.yml build; \
	elif [ "$(TRANSCRIPTION)" = "gpu" ]; then \
		cd services/transcription-service && docker compose build; \
	fi

# Build Docker Compose service images
build: check_docker build-bot-image build-transcription-service
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	docker compose $$COMPOSE_FILES --profile remote build

# Start transcription-service based on TRANSCRIPTION
up-transcription-service: check_docker
	@if [ "$(TRANSCRIPTION)" = "remote" ]; then \
		exit 0; \
	elif [ "$(TRANSCRIPTION)" = "cpu" ]; then \
		cd services/transcription-service && docker compose -f docker-compose.cpu.yml up -d; \
	elif [ "$(TRANSCRIPTION)" = "gpu" ]; then \
		cd services/transcription-service && docker compose up -d; \
	fi

# Stop transcription-service
down-transcription-service: check_docker
	@cd services/transcription-service && docker compose -f docker-compose.cpu.yml down 2>/dev/null || true
	@cd services/transcription-service && docker compose down 2>/dev/null || true

# Start services in detached mode
up: check_docker
	@if [ "$(TRANSCRIPTION)" != "remote" ]; then \
		$(MAKE) up-transcription-service; \
	fi
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	if ! docker network ls | grep -q "vexa-network"; then \
		echo "Creating vexa-network..."; \
		docker network create vexa-network || true; \
	fi; \
	docker compose $$COMPOSE_FILES --profile remote up -d; \
	sleep 3; \
	if [ "$$REMOTE_DB" = "true" ]; then \
		if docker compose $$COMPOSE_FILES ps -q postgres 2>/dev/null | grep -q .; then \
			echo "WARNING: postgres container is running but REMOTE_DB=true"; \
			exit 1; \
		fi; \
	else \
		if ! docker compose $$COMPOSE_FILES ps -q postgres 2>/dev/null | grep -q .; then \
			echo "ERROR: postgres container failed to start. Check logs with: docker compose $$COMPOSE_FILES logs postgres"; \
			exit 1; \
		fi; \
		echo "✓ Local postgres container is running"; \
	fi

# Stop services
down: check_docker down-transcription-service
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	docker compose $$COMPOSE_FILES down

# Show container status
ps: check_docker
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	docker compose $$COMPOSE_FILES ps

# Tail logs for all services
logs:
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	docker compose $$COMPOSE_FILES logs -f

# Run the interaction test script
test: check_docker
	@if [ -f .env ]; then \
		API_PORT=$$(grep -E '^[[:space:]]*API_GATEWAY_HOST_PORT=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "8056"); \
		ADMIN_PORT=$$(grep -E '^[[:space:]]*ADMIN_API_HOST_PORT=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "8057"); \
		echo "API: http://localhost:$$API_PORT/docs"; \
		echo "Admin: http://localhost:$$ADMIN_PORT/docs"; \
	else \
		echo "API: http://localhost:8056/docs"; \
		echo "Admin: http://localhost:8057/docs"; \
	fi
	@chmod +x testing/run_vexa_interaction.sh
	@if [ -f .env ]; then \
		DEVICE_TYPE=$$(grep -E '^[[:space:]]*DEVICE_TYPE=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//'); \
		WHISPER_MODEL=$$(grep -E '^[[:space:]]*WHISPER_MODEL_SIZE=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//'); \
		if [ "$$DEVICE_TYPE" = "cpu" ] || [ "$$DEVICE_TYPE" = "" ]; then \
			echo "⚠️  WARNING: CPU mode - DEVELOPMENT ONLY"; \
		fi; \
	fi
	@if [ -n "$(MEETING_ID)" ]; then \
		./testing/run_vexa_interaction.sh "$(MEETING_ID)"; \
	else \
		./testing/run_vexa_interaction.sh; \
	fi

# Quick API connectivity test
test-api: check_docker
	@API_PORT=$$(grep -E '^[[:space:]]*API_GATEWAY_HOST_PORT=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "8056"); \
	ADMIN_PORT=$$(grep -E '^[[:space:]]*ADMIN_API_HOST_PORT=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "8057"); \
	if ! curl -s -f "http://localhost:$$API_PORT/docs" > /dev/null; then \
		echo "❌ API Gateway not responding"; \
		exit 1; \
	fi; \
	if ! curl -s -f "http://localhost:$$ADMIN_PORT/docs" > /dev/null; then \
		echo "❌ Admin API not responding"; \
		exit 1; \
	fi; \
	echo "✅ API connectivity test passed"

# Test system setup without requiring meeting ID
test-setup: check_docker
	@if [ -f .env ]; then \
		API_PORT=$$(grep -E '^[[:space:]]*API_GATEWAY_HOST_PORT=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "8056"); \
		ADMIN_PORT=$$(grep -E '^[[:space:]]*ADMIN_API_HOST_PORT=' .env | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "8057"); \
		echo "API: http://localhost:$$API_PORT/docs"; \
		echo "Admin: http://localhost:$$ADMIN_PORT/docs"; \
	fi
	@$(MAKE) test-api
	@echo "Ready for testing. Use 'make test MEETING_ID=your-meeting-id'"

# --- Database Migration Commands ---

# Smart migration: detects if database is fresh, legacy, or already Alembic-managed
migrate-or-init: check_docker
	@set -e; \
	REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	DB_HOST=$$(grep -E '^[[:space:]]*DB_HOST=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "postgres"); \
	DB_PORT=$$(grep -E '^[[:space:]]*DB_PORT=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "5432"); \
	DB_NAME=$$(grep -E '^[[:space:]]*DB_NAME=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "vexa"); \
	DB_USER=$$(grep -E '^[[:space:]]*DB_USER=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "postgres"); \
	[ -n "$$DB_NAME" ] || DB_NAME="vexa"; \
	[ -n "$$DB_USER" ] || DB_USER="postgres"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		if ! docker compose $$COMPOSE_FILES ps -q postgres | grep -q .; then \
			echo "ERROR: PostgreSQL container is not running. Run 'make up' first."; \
			exit 1; \
		fi; \
		count=0; \
		while ! docker compose $$COMPOSE_FILES exec -T postgres pg_isready -U $$DB_USER -d $$DB_NAME -q; do \
			if [ $$count -ge 12 ]; then \
				echo "ERROR: Database did not become ready in 60 seconds."; \
				exit 1; \
			fi; \
			sleep 5; \
			count=$$((count+1)); \
		done; \
		docker compose $$COMPOSE_FILES exec -T transcription-collector python /app/libs/shared-models/fix_alembic_version.py --repair-stale; \
		HAS_ALEMBIC_TABLE=$$(docker compose $$COMPOSE_FILES exec -T postgres psql -U $$DB_USER -d $$DB_NAME -t -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version';" 2>/dev/null | tr -d '[:space:]' || echo ""); \
		if [ "$$HAS_ALEMBIC_TABLE" = "1" ]; then \
			$(MAKE) migrate; \
		else \
			docker compose $$COMPOSE_FILES exec -T transcription-collector python -c "import asyncio; from shared_models.database import init_db; asyncio.run(init_db())"; \
			docker compose $$COMPOSE_FILES exec -T transcription-collector python /app/libs/shared-models/fix_alembic_version.py --create-if-missing; \
		fi; \
	else \
		docker compose $$COMPOSE_FILES exec -T transcription-collector python /app/libs/shared-models/fix_alembic_version.py --repair-stale; \
		DB_STATE=$$(docker compose $$COMPOSE_FILES exec -T transcription-collector python /app/libs/shared-models/check_db_state.py 2>/dev/null || echo "fresh"); \
		if [ "$$DB_STATE" = "alembic" ]; then \
			$(MAKE) migrate; \
		else \
			docker compose $$COMPOSE_FILES exec -T transcription-collector python -c "import asyncio; from shared_models.database import init_db; asyncio.run(init_db())"; \
			docker compose $$COMPOSE_FILES exec -T transcription-collector python /app/libs/shared-models/fix_alembic_version.py --create-if-missing; \
		fi; \
	fi

# Apply all pending migrations
migrate: check_docker
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	if [ "$$REMOTE_DB" != "true" ]; then \
		if ! docker compose -f docker-compose.yml -f docker-compose.local-db.yml ps postgres | grep -q "Up"; then \
			echo "ERROR: PostgreSQL container is not running. Run 'make up' first."; \
			exit 1; \
		fi; \
		DB_USER=$$(grep -E '^[[:space:]]*DB_USER=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "postgres"); \
		DB_NAME=$$(grep -E '^[[:space:]]*DB_NAME=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' || echo "vexa"); \
		[ -n "$$DB_USER" ] || DB_USER="postgres"; \
		[ -n "$$DB_NAME" ] || DB_NAME="vexa"; \
		docker compose -f docker-compose.yml -f docker-compose.local-db.yml exec -T transcription-collector python /app/libs/shared-models/fix_alembic_version.py --repair-stale; \
		current_version=$$(docker compose -f docker-compose.yml -f docker-compose.local-db.yml exec -T transcription-collector alembic -c /app/alembic.ini current 2>/dev/null | grep -E '^[a-f0-9]{12}' | head -1 || echo ""); \
		if [ "$$current_version" = "dc59a1c03d1f" ]; then \
			if docker compose -f docker-compose.yml -f docker-compose.local-db.yml exec -T postgres psql -U $$DB_USER -d $$DB_NAME -t -c "SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'data';" | grep -q 1; then \
				docker compose -f docker-compose.yml -f docker-compose.local-db.yml exec -T transcription-collector alembic -c /app/alembic.ini stamp 5befe308fa8b; \
			fi; \
		fi; \
		docker compose -f docker-compose.yml -f docker-compose.local-db.yml exec -T transcription-collector alembic -c /app/alembic.ini upgrade head; \
	else \
		docker compose -f docker-compose.yml exec -T transcription-collector alembic -c /app/alembic.ini upgrade head; \
	fi

# Create a new migration file
makemigrations: check_docker
	@if [ -z "$(M)" ]; then \
		echo "Usage: make makemigrations M=\"your migration message\""; \
		exit 1; \
	fi
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
		if ! docker compose $$COMPOSE_FILES ps postgres | grep -q "Up"; then \
			echo "ERROR: PostgreSQL container is not running. Run 'make up' first."; \
			exit 1; \
		fi; \
	fi
	@docker compose $$COMPOSE_FILES exec -T transcription-collector alembic -c /app/alembic.ini revision --autogenerate -m "$(M)"

# Initialize the database
init-db: check_docker
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
	fi; \
	docker compose $$COMPOSE_FILES run --rm transcription-collector python -c "import asyncio; from shared_models.database import init_db; asyncio.run(init_db())"; \
	docker compose $$COMPOSE_FILES run --rm transcription-collector alembic -c /app/alembic.ini stamp head

# Stamp existing database with current version
stamp-db: check_docker
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
		if ! docker compose $$COMPOSE_FILES ps postgres | grep -q "Up"; then \
			echo "ERROR: PostgreSQL container is not running. Run 'make up' first."; \
			exit 1; \
		fi; \
	fi
	@docker compose $$COMPOSE_FILES exec -T transcription-collector alembic -c /app/alembic.ini stamp head

# Show current migration status
migration-status: check_docker
	@REMOTE_DB=$$(grep -E '^[[:space:]]*REMOTE_DB=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//' | tr '[:upper:]' '[:lower:]' || echo "false"); \
	COMPOSE_FILES="-f docker-compose.yml"; \
	if [ "$$REMOTE_DB" != "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.local-db.yml"; \
		if ! docker compose $$COMPOSE_FILES ps postgres | grep -q "Up"; then \
			echo "ERROR: PostgreSQL container is not running. Run 'make up' first."; \
			exit 1; \
		fi; \
	fi
	@docker compose $$COMPOSE_FILES exec -T transcription-collector alembic -c /app/alembic.ini current
	@docker compose $$COMPOSE_FILES exec -T transcription-collector alembic -c /app/alembic.ini history --verbose
