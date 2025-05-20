# Vexa - Local Deployment and Testing Guide

This document provides concise instructions for setting up, running, and testing the Vexa system locally using Docker Compose and Make.

### Prerequisites

- Docker and Docker Compose
- Git
- Python 3.x (for model download script)
- `make` utility
- `jq` (for the testing script, e.g., `sudo apt-get install jq`)
- For GPU support: NVIDIA Container Toolkit

### Quick Start with Make

The `Makefile` automates setup. The `.env` file (created from `env-example.cpu` or `env-example.gpu`) is central to configuration, defining `ADMIN_API_TOKEN`, `DEVICE_TYPE`, `WHISPER_MODEL_SIZE`, and **exposed host ports** for services like `API_GATEWAY_HOST_PORT`, `ADMIN_API_HOST_PORT`, etc.

**Crucial First Step: Configure `.env`**

1.  Create/update the `.env` file for your target:
    - For CPU: `make env TARGET=cpu` (or just `make env`)
    - For GPU: `make env TARGET=gpu`
2.  **Edit `vexa_cpu/.env` and set a secure `ADMIN_API_TOKEN`**. Also, **verify/adjust the `*_HOST_PORT` variables** if you need to change default exposed ports.
    Example: `ADMIN_API_TOKEN=your_secret_token` and `API_GATEWAY_HOST_PORT=8056`.

**Running the System:**

1.  **For CPU (Tiny Model, Slower Performance - Good for local tests/development):**
    ```bash
    # Ensure .env is configured (ADMIN_API_TOKEN, ports, etc.)
    make all TARGET=cpu 
    # Or simply 'make all' if .env reflects CPU settings and is fully configured.
    ```
    This command (among other things) uses `env-example.cpu` defaults for `.env` if not present.

2.  **For GPU (Medium Model, Faster Performance - Requires NVIDIA GPU & Toolkit):**
    ```bash
    # Ensure .env is configured (ADMIN_API_TOKEN, ports, etc.)
    make all TARGET=gpu
    ```
    This uses `env-example.gpu` defaults for `.env` if not present.

**Managing Services:**
- `make ps`: Show container status.
- `make logs`: Tail logs (or `make logs SERVICE=<service_name>`).
- `make down`: Stop all services.
- `make clean`: Stop services and remove volumes.

### Script for easy first testing

Once services are running, test with `./run_vexa_interaction.sh` (ensure it's executable: `chmod +x run_vexa_interaction.sh`).
- It reads `ADMIN_API_TOKEN` and **host ports** from `vexa_cpu/.env`.
- Guides through user/token creation, bot dispatch to a Google Meet ID.
- **Admit the bot** when prompted by the script (10s countdown).
- `Ctrl+C` stops the script and bot.

### API Documentation

API docs (Swagger/OpenAPI) are typically available at (ports are configurable in `.env`):
- Main API: `http://localhost:${API_GATEWAY_HOST_PORT:-8056}/docs`
- Admin API: `http://localhost:${ADMIN_API_HOST_PORT:-8057}/docs`

