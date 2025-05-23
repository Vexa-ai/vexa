.PHONY: all setup submodules env download-model build-bot-image build up down clean ps logs

# Default target: Sets up everything and starts the services
all: setup build up

# Target to perform all initial setup steps
setup: submodules env download-model build-bot-image
	@echo "Setup complete. Please ensure you have edited the .env file if necessary."
	@echo "To specify a target for .env generation (cpu or gpu), run 'make env TARGET=cpu' or 'make env TARGET=gpu' first, then 'make setup'."
	@echo "If no TARGET is specified for 'make setup', it will default to 'make env TARGET=cpu'."

# Initialize and update Git submodules
# submodules:
# 	@echo "---> Initializing and updating Git submodules..."
# 	@git submodule update --init --recursive

# Create .env file from example
env:
ifndef TARGET
	$(info TARGET not set. Defaulting to cpu. Use 'make env TARGET=cpu' or 'make env TARGET=gpu')
	$(eval TARGET := cpu)
endif
	@echo "---> Creating .env file for TARGET=$(TARGET)..."
	@if [ "$(TARGET)" = "cpu" ]; then \
		if [ ! -f env-example.cpu ]; then \
			echo "env-example.cpu not found. Creating default one."; \
			echo "ADMIN_API_TOKEN=token" > env-example.cpu; \
			echo "LANGUAGE_DETECTION_SEGMENTS=10" >> env-example.cpu; \
			echo "VAD_FILTER_THRESHOLD=0.5" >> env-example.cpu; \
			echo "WHISPER_MODEL_SIZE=tiny" >> env-example.cpu; \
			echo "DEVICE_TYPE=cpu" >> env-example.cpu; \
			echo "# Exposed Host Ports" >> env-example.cpu; \
			echo "API_GATEWAY_HOST_PORT=8056" >> env-example.cpu; \
			echo "ADMIN_API_HOST_PORT=8057" >> env-example.cpu; \
			echo "TRAEFIK_WEB_HOST_PORT=9090" >> env-example.cpu; \
			echo "TRAEFIK_DASHBOARD_HOST_PORT=8085" >> env-example.cpu; \
			echo "TRANSCRIPTION_COLLECTOR_HOST_PORT=8123" >> env-example.cpu; \
			echo "POSTGRES_HOST_PORT=5438" >> env-example.cpu; \
		fi; \
		cp env-example.cpu .env; \
		echo "*** .env file created from env-example.cpu. Please review it. ***"; \
	elif [ "$(TARGET)" = "gpu" ]; then \
		if [ ! -f env-example.gpu ]; then \
			echo "env-example.gpu not found. Creating default one."; \
			echo "ADMIN_API_TOKEN=token" > env-example.gpu; \
			echo "LANGUAGE_DETECTION_SEGMENTS=10" >> env-example.gpu; \
			echo "VAD_FILTER_THRESHOLD=0.5" >> env-example.gpu; \
			echo "WHISPER_MODEL_SIZE=medium" >> env-example.gpu; \
			echo "DEVICE_TYPE=cuda" >> env-example.gpu; \
			echo "# Exposed Host Ports" >> env-example.gpu; \
			echo "API_GATEWAY_HOST_PORT=8056" >> env-example.gpu; \
			echo "ADMIN_API_HOST_PORT=8057" >> env-example.gpu; \
			echo "TRAEFIK_WEB_HOST_PORT=9090" >> env-example.gpu; \
			echo "TRAEFIK_DASHBOARD_HOST_PORT=8085" >> env-example.gpu; \
			echo "TRANSCRIPTION_COLLECTOR_HOST_PORT=8123" >> env-example.gpu; \
			echo "POSTGRES_HOST_PORT=5438" >> env-example.gpu; \
		fi; \
		cp env-example.gpu .env; \
		echo "*** .env file created from env-example.gpu. Please review it. ***"; \
	else \
		echo "Error: TARGET must be 'cpu' or 'gpu'. Usage: make env TARGET=<cpu|gpu>"; \
		exit 1; \
	fi

# Download the Whisper model
download-model:
	@echo "---> Creating ./hub directory if it doesn't exist..."
	@mkdir -p ./hub
	@echo "---> Ensuring ./hub directory is writable..."
	@chmod u+w ./hub
	@echo "---> Downloading Whisper model (this may take a while)..."
	@python download_model.py

# Build the standalone vexa-bot image
build-bot-image:
	@echo "---> Building vexa-bot:latest image..."
	@docker build -t vexa-bot:latest -f services/vexa-bot/core/Dockerfile ./services/vexa-bot/core

# Build Docker Compose service images
build:
ifndef TARGET
	$(info TARGET not set for 'build'. Defaulting to cpu.)
	$(eval TARGET := cpu)
endif
	@echo "---> Building Docker Compose services..."
	@if [ "$(TARGET)" = "cpu" ]; then \
		echo "---> Building with 'cpu' profile (includes whisperlive-cpu)..."; \
		docker compose --profile cpu build; \
	elif [ "$(TARGET)" = "gpu" ]; then \
		echo "---> Building with 'gpu' profile (includes whisperlive for GPU)..."; \
		docker compose --profile gpu build; \
	else \
		echo "---> Building services without a specific cpu/gpu profile. This might include all services not assigned to a profile."; \
		docker compose build; \
	fi

# Start services in detached mode
up:
ifndef TARGET
	$(info TARGET not set for 'up'. Defaulting to cpu.)
	$(eval TARGET := cpu)
endif
	@echo "---> Starting Docker Compose services..."
	@if [ "$(TARGET)" = "cpu" ]; then \
		echo "---> Activating 'cpu' profile to start whisperlive-cpu along with other services..."; \
		docker compose --profile cpu up; \
	elif [ "$(TARGET)" = "gpu" ]; then \
		echo "---> Activating 'gpu' profile to start whisperlive (for GPU) along with other services..."; \
		docker compose --profile gpu up; \
	else \
		echo "---> TARGET not explicitly cpu or gpu. Starting default services. Profiled services (cpu/gpu specific) may not start correctly."; \
		docker compose up; \
	fi

# Stop services
down:
	@echo "---> Stopping Docker Compose services..."
	@docker compose down

# Stop services and remove volumes
clean:
	@echo "---> Stopping Docker Compose services and removing volumes..."
	@docker compose down -v

# Show container status
ps:
	@docker compose ps

# Tail logs for all services
logs:
	@docker compose logs -f
