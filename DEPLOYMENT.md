# Vexa - Local Deployment and Testing Guide

This document provides concise instructions for setting up, running, and testing the Vexa system locally using Docker Compose and Make.


### Quick Start with Make


1.  **For CPU (Tiny Model, Slower Performance - Good for local tests/development):**
    ```bash
    make all
    ```
    This command (among other things) uses `env-example.cpu` defaults for `.env` if not present.

2.  **For GPU (Medium Model, Faster Performance - Requires NVIDIA GPU & Toolkit):**
    ```bash
    make all TARGET=gpu
    ```
    This uses `env-example.gpu` defaults for `.env` if not present.


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

**Managing Services:**
- `make ps`: Show container status.
- `make logs`: Tail logs (or `make logs SERVICE=<service_name>`).
- `make down`: Stop all services.
- `make clean`: Stop services and remove volumes.

