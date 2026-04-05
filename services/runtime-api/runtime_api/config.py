"""Configuration from environment variables."""

import os

# Backend selection
ORCHESTRATOR_BACKEND = os.getenv("ORCHESTRATOR_BACKEND", "docker")

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Docker backend
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "bridge")

# Kubernetes backend
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", os.getenv("POD_NAMESPACE", "default"))
K8S_SERVICE_ACCOUNT = os.getenv("K8S_SERVICE_ACCOUNT", "")
K8S_IMAGE_PULL_POLICY = os.getenv("K8S_IMAGE_PULL_POLICY", "IfNotPresent")
K8S_IMAGE_PULL_SECRET = os.getenv("K8S_IMAGE_PULL_SECRET", "")

# Process backend
PROCESS_LOGS_DIR = os.getenv("PROCESS_LOGS_DIR", "/var/log/containers")
PROCESS_REAPER_INTERVAL = int(os.getenv("PROCESS_REAPER_INTERVAL", "30"))

# Profiles
PROFILES_PATH = os.getenv("PROFILES_PATH", "profiles.yaml")

# Lifecycle
IDLE_CHECK_INTERVAL = int(os.getenv("IDLE_CHECK_INTERVAL", "30"))
CALLBACK_RETRIES = int(os.getenv("CALLBACK_RETRIES", "3"))
CALLBACK_BACKOFF = [float(x) for x in os.getenv("CALLBACK_BACKOFF", "1,5,30").split(",")]
ALLOW_PRIVATE_CALLBACKS = os.getenv("ALLOW_PRIVATE_CALLBACKS", "").lower() in ("1", "true", "yes")

# Auth
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]

# Server
SCHEDULER_POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "5"))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8090"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
