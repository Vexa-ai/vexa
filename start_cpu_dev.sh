#!/bin/bash

# Script to start Vexa services for local CPU development

echo "Bringing down any existing services..."
docker compose down --remove-orphans

echo ""
echo "Starting essential services: redis, postgres, transcription-collector..."
docker compose up -d redis postgres transcription-collector

echo ""
echo "Starting WhisperLive CPU version (with --force-recreate)..."
docker compose --profile cpu up -d --force-recreate whisperlive-cpu

echo ""
echo "Starting API services: admin-api, bot-manager, api-gateway, and traefik..."
docker compose up -d admin-api bot-manager api-gateway traefik

echo ""
echo "CPU development environment startup sequence complete."
echo "You can check service status with: docker compose ps" 