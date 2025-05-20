# Vexa - Deployment and Local Setup Guide

This document provides instructions for setting up and running the Vexa system in a local development environment using Docker Compose.

## System Architecture (Relevant to Deployment)

The system consists of several key microservices orchestrated via Docker Compose:

1.  **API Gateway (`api-gateway`)** - The main entry point (`http://localhost:8056`) for client requests.
2.  **Admin API (`admin-api`)** - Handles administrative tasks (`http://localhost:8057`). Requires `X-Admin-API-Key` header set in `.env` (`ADMIN_API_TOKEN`).
3.  **Bot Manager (`bot-manager`)** - Manages the lifecycle of Vexa Bot containers, interacting with the Docker daemon.
4.  **Transcription Collector (`transcription-collector`)** - Receives transcription segments via WebSocket from WhisperLive.
5.  **WhisperLive (`whisperlive`)** - Performs live speech-to-text transcription (`http://localhost:9090`). Requires downloaded model and potentially GPU.
6.  **Vexa Bot Image (`vexa-bot:latest`)** - The Docker image for the bot, built manually and launched on demand by Bot Manager.
7.  **Redis** - Used for caching and locking.
8.  **PostgreSQL (`postgres`)** - Primary database (`localhost:5438`).

## Local Development Setup

### Prerequisites

- Docker and Docker Compose installed
- Git (with Git LFS potentially needed by submodules)
- Python 3.x (for the model download script)
- `make` utility installed (for using the Makefile shortcuts)
- NVIDIA Container Toolkit (if using `whisperlive` with GPU acceleration)

### Quick Start with Make

A `Makefile` is provided to automate common setup and management tasks. After cloning the repository and `cd`-ing into the directory:

1.  **Run Initial Setup:**

    ```bash
    make setup
    ```

    This command will:

    - Initialize Git submodules (`make submodules`).
    - Create `.env` from `env-example` if it doesn't exist (`make env`). **You MUST manually edit the `.env` file now to set your configuration (e.g., `ADMIN_API_TOKEN`).**
    - Download the Whisper model (`make download-model`).
    - Build the required `vexa-bot:latest` image (`make build-bot-image`).

2.  **Build and Start Services:**

    ```bash
    make build  # Build service images defined in docker-compose.yml
    make up     # Start services in detached mode
    ```

    Alternatively, to run the full setup, build, and start sequence in one command (after cloning):

    ```bash
    make all
    # Remember to edit the .env file after the 'make env' step runs!
    ```

3.  **Common Management Commands:**
    - `make down`: Stop all services.
    - `make clean`: Stop services and remove associated volumes.
    - `make ps`: Show container status.
    - `make logs`: Tail logs for all services.

_(See the section below for detailed step-by-step instructions if you prefer not to use `make`)_.

### Getting Started (Step-by-Step)

1.  **Clone the Repository:**
    (Ensure you have the main Vexa repository cloned)

    ```bash
    cd path/to/vexa
    ```

2.  **Initialize Submodules:**
    This project uses Git submodules (`services/vexa-bot` and `services/WhisperLive`). Initialize and clone them:

    ```bash
    git submodule update --init --recursive
    ```

3.  **Configure Environment:**
    Create a `.env` file from the example and customize it with your settings (especially `ADMIN_API_TOKEN`).

    ```bash
    cp env-example .env
    # Now edit the .env file with your preferred editor
    nano .env
    ```

4.  **Download Whisper Model:**
    The `whisperlive` service requires a pre-trained Whisper model. Run the provided script to download it (adjust model name in script/compose file if needed).

    [Optional] Set up a virtual environment before running the download model script to avoid installing the dependencies globally:

    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

    Run the script to download the model:

    ```bash
    python download_model.py
    ```

    This will download the model to the `./hub` directory, which is mounted into the `whisperlive` container.

5.  **Build Vexa Bot Image:**
    The Vexa Bot image needs to be built manually before starting the system, as it's launched dynamically by the Bot Manager. Ensure submodules are initialized first (Step 2).

    ```bash
    docker build -t vexa-bot:latest -f services/vexa-bot/core/Dockerfile ./services/vexa-bot/core
    ```

6.  **Build and Start Services:**
    Use Docker Compose to build the other service images and start the system. It will use the values from your `.env` file.

    ```bash
    # Build service images (excluding vexa-bot which was built in the previous step)
    docker compose build

    # Start all services in detached mode
    docker compose up -d
    ```

    This will start all services defined in `docker-compose.yml`.

7.  **Accessing Services:**

    - **Main API:** `http://localhost:8056` (API Gateway)
    - **Admin API (Direct Dev Access):** `http://localhost:8057`
    - **WhisperLive (Debug):** `http://localhost:9090`
    - **Transcription Collector (Debug):** `http://localhost:8123` (Maps to container port 8000)
    - **PostgreSQL (Direct Dev Access):** `localhost:5438` (Maps to container port 5432)
      Internal services (Bot Manager, Transcription Collector, Redis, Postgres) communicate over the Docker network and are not directly exposed by default (except for the debug/direct access ports listed above).

8.  **Check Status:**

    ```bash
    docker compose ps
    ```

9.  **View Logs:**

    ```bash
    # Tail logs for all services
    docker compose logs -f

    # Tail logs for a specific service
    docker compose logs -f api-gateway
    docker compose logs -f bot-manager
    # etc...
    ```

### API Usage (Development Environment)

- **Admin Operations:** Access via `http://localhost:8057` or `http://localhost:8056/admin/...`. Requires the `X-Admin-API-Key` header (value set as `ADMIN_API_TOKEN` in the project's `.env` file - see Step 3 in Getting Started).
  - Example: `curl -H "X-Admin-API-Key: YOUR_ADMIN_TOKEN_FROM_DOTENV" http://localhost:8057/admin/users`
- **Client Operations:** Access via the gateway `http://localhost:8056`. Requires the `X-API-Key` header (value corresponds to a token generated via the admin API).
  - Example (Request Bot):
    ```bash
    curl -X POST http://localhost:8056/bots \\
      -H "Content-Type: application/json" \\
      -H "X-API-Key: YOUR_CLIENT_API_KEY" \\
      -d '{
            "platform": "google_meet",
            "meeting_url": "https://meet.google.com/your-meeting-code",
            "token": "some_customer_or_request_token",
            "bot_name": "VexaHelper"
      }'
    ```
  - Example (Get Meetings):
    ```bash
    curl -H "X-API-Key: YOUR_CLIENT_API_KEY" http://localhost:8056/meetings
    ```
